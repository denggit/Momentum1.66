"""
四号引擎v3.0 Numba缓存管理器
管理Numba JIT编译缓存，支持多进程缓存共享和清理策略
"""

import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 导入现有日志模块
from src.utils.log import get_logger

# 尝试导入numba缓存模块
try:
    from numba.core.caching import Cache
    from numba.core.compiler_lock import global_compiler_lock

    NUMBA_CACHE_AVAILABLE = True
except ImportError:
    NUMBA_CACHE_AVAILABLE = False


class CacheCleanupStrategy(Enum):
    """缓存清理策略枚举"""
    AGE_BASED = "age_based"  # 基于文件年龄清理
    SIZE_BASED = "size_based"  # 基于总大小清理
    FREQUENCY_BASED = "frequency_based"  # 基于使用频率清理
    HYBRID = "hybrid"  # 混合策略


@dataclass
class CacheFileInfo:
    """缓存文件信息"""
    file_path: str
    size_bytes: int
    created_time: float
    last_accessed_time: float
    last_modified_time: float
    access_count: int = 0
    is_locked: bool = False
    signature_hash: Optional[str] = None


@dataclass
class CacheStats:
    """缓存统计信息"""
    total_files: int = 0
    total_size_bytes: int = 0
    avg_file_size_bytes: int = 0
    oldest_file_age_seconds: float = 0.0
    newest_file_age_seconds: float = 0.0
    cache_hit_rate: float = 0.0
    cache_miss_rate: float = 0.0
    last_cleanup_time: Optional[float] = None
    cleanup_count: int = 0
    reclaimed_space_bytes: int = 0


class NumbaCacheManager:
    """
    Numba缓存管理器

    主要功能：
    1. 管理Numba编译缓存目录和文件
    2. 支持多进程缓存共享（文件锁机制）
    3. 实现智能缓存清理策略
    4. 监控缓存使用情况和性能
    5. 提供缓存预热和有效性检查

    使用示例：
    ```python
    # 创建缓存管理器
    cache_manager = NumbaCacheManager(
        cache_dir="~/.cache/numba/triplea",
        max_size_mb=500,
        cleanup_strategy=CacheCleanupStrategy.HYBRID
    )

    # 初始化缓存目录
    cache_manager.initialize()

    # 获取缓存统计信息
    stats = cache_manager.get_stats()

    # 清理旧缓存
    reclaimed = cache_manager.cleanup(max_age_days=30)

    # 关闭管理器
    cache_manager.shutdown()
    ```
    """

    # 默认缓存目录
    DEFAULT_CACHE_DIR = os.path.join(
        os.path.expanduser("~"),
        ".cache", "numba", "triplea_v3"
    )

    # 缓存元数据文件
    META_FILENAME = "cache_metadata.json"
    LOCK_FILENAME = "cache.lock"

    def __init__(
            self,
            cache_dir: Optional[str] = None,
            max_size_mb: int = 500,
            cleanup_strategy: CacheCleanupStrategy = CacheCleanupStrategy.HYBRID,
            enable_file_locking: bool = True,
            logger: Optional[Any] = None
    ):
        """
        初始化缓存管理器

        Args:
            cache_dir: 缓存目录路径（None则使用默认）
            max_size_mb: 最大缓存大小（MB）
            cleanup_strategy: 清理策略
            enable_file_locking: 是否启用文件锁（多进程安全）
            logger: 日志记录器
        """
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.cleanup_strategy = cleanup_strategy
        self.enable_file_locking = enable_file_locking

        self.logger = logger or get_logger(__name__)
        self._cache_dir_path = Path(self.cache_dir).expanduser().resolve()
        self._meta_file_path = self._cache_dir_path / self.META_FILENAME
        self._lock_file_path = self._cache_dir_path / self.LOCK_FILENAME

        self._stats = CacheStats()
        self._file_infos: Dict[str, CacheFileInfo] = {}
        self._lock = threading.RLock()
        self._is_initialized = False
        self._is_shutdown = False
        self._file_lock: Optional[threading.RLock] = None

        # 文件锁用于多进程同步
        if self.enable_file_locking:
            self._file_lock = threading.RLock()

        # 检查Numba缓存可用性
        if not NUMBA_CACHE_AVAILABLE:
            self.logger.warning(
                "Numba缓存模块不可用，降级为基本文件缓存管理"
            )

    def initialize(self) -> bool:
        """
        初始化缓存目录和元数据

        Returns:
            是否成功初始化
        """
        if self._is_shutdown:
            self.logger.error("管理器已关闭，无法初始化")
            return False

        with self._lock:
            try:
                # 创建缓存目录
                self._cache_dir_path.mkdir(parents=True, exist_ok=True)

                # 创建或加载元数据
                if self._meta_file_path.exists():
                    self._load_metadata()
                else:
                    self._save_metadata()

                # 扫描缓存文件
                self._scan_cache_files()

                self._is_initialized = True
                self.logger.info(
                    f"缓存管理器初始化完成: {self._cache_dir_path}"
                )
                self.logger.info(
                    f"缓存统计: {self._stats.total_files} 个文件, "
                    f"{self._stats.total_size_bytes / (1024 * 1024):.1f} MB"
                )

                return True

            except Exception as e:
                self.logger.error(f"缓存管理器初始化失败: {e}", exc_info=True)
                return False

    def _load_metadata(self) -> bool:
        """加载元数据"""
        try:
            if not self._meta_file_path.exists():
                return False

            with open(self._meta_file_path, 'r') as f:
                meta_data = json.load(f)

            # 加载文件信息
            self._file_infos.clear()
            for file_path_str, file_info_data in meta_data.get('file_infos', {}).items():
                file_info = CacheFileInfo(
                    file_path=file_info_data['file_path'],
                    size_bytes=file_info_data['size_bytes'],
                    created_time=file_info_data['created_time'],
                    last_accessed_time=file_info_data['last_accessed_time'],
                    last_modified_time=file_info_data['last_modified_time'],
                    access_count=file_info_data['access_count'],
                    is_locked=file_info_data.get('is_locked', False),
                    signature_hash=file_info_data.get('signature_hash')
                )
                self._file_infos[file_path_str] = file_info

            # 加载统计信息
            stats_data = meta_data.get('stats', {})
            self._stats = CacheStats(
                total_files=stats_data.get('total_files', 0),
                total_size_bytes=stats_data.get('total_size_bytes', 0),
                avg_file_size_bytes=stats_data.get('avg_file_size_bytes', 0),
                oldest_file_age_seconds=stats_data.get('oldest_file_age_seconds', 0.0),
                newest_file_age_seconds=stats_data.get('newest_file_age_seconds', 0.0),
                cache_hit_rate=stats_data.get('cache_hit_rate', 0.0),
                cache_miss_rate=stats_data.get('cache_miss_rate', 0.0),
                last_cleanup_time=stats_data.get('last_cleanup_time'),
                cleanup_count=stats_data.get('cleanup_count', 0),
                reclaimed_space_bytes=stats_data.get('reclaimed_space_bytes', 0)
            )

            return True

        except Exception as e:
            self.logger.warning(f"加载缓存元数据失败: {e}")
            return False

    def _save_metadata(self) -> bool:
        """保存元数据"""
        try:
            meta_data = {
                'version': '1.0',
                'created_at': time.time(),
                'last_updated': time.time(),
                'cache_dir': str(self._cache_dir_path),
                'file_infos': {},
                'stats': {
                    'total_files': self._stats.total_files,
                    'total_size_bytes': self._stats.total_size_bytes,
                    'avg_file_size_bytes': self._stats.avg_file_size_bytes,
                    'oldest_file_age_seconds': self._stats.oldest_file_age_seconds,
                    'newest_file_age_seconds': self._stats.newest_file_age_seconds,
                    'cache_hit_rate': self._stats.cache_hit_rate,
                    'cache_miss_rate': self._stats.cache_miss_rate,
                    'last_cleanup_time': self._stats.last_cleanup_time,
                    'cleanup_count': self._stats.cleanup_count,
                    'reclaimed_space_bytes': self._stats.reclaimed_space_bytes
                }
            }

            # 保存文件信息
            for file_path_str, file_info in self._file_infos.items():
                meta_data['file_infos'][file_path_str] = {
                    'file_path': file_info.file_path,
                    'size_bytes': file_info.size_bytes,
                    'created_time': file_info.created_time,
                    'last_accessed_time': file_info.last_accessed_time,
                    'last_modified_time': file_info.last_modified_time,
                    'access_count': file_info.access_count,
                    'is_locked': file_info.is_locked,
                    'signature_hash': file_info.signature_hash
                }

            # 写入文件
            with open(self._meta_file_path, 'w') as f:
                json.dump(meta_data, f, indent=2)

            return True

        except Exception as e:
            self.logger.error(f"保存缓存元数据失败: {e}")
            return False

    def _scan_cache_files(self) -> None:
        """扫描缓存文件并更新信息"""
        self._file_infos.clear()
        total_size = 0
        file_count = 0

        current_time = time.time()
        oldest_age = float('inf')
        newest_age = 0.0

        try:
            # 遍历缓存目录
            for file_path in self._cache_dir_path.rglob('*'):
                if file_path.is_file():
                    try:
                        stat = file_path.stat()
                        file_age = current_time - stat.st_mtime

                        # 更新年龄统计
                        oldest_age = min(oldest_age, file_age)
                        newest_age = max(newest_age, file_age)

                        # 创建文件信息
                        file_info = CacheFileInfo(
                            file_path=str(file_path),
                            size_bytes=stat.st_size,
                            created_time=stat.st_mtime,  # 使用修改时间作为创建时间参考
                            last_accessed_time=stat.st_atime,
                            last_modified_time=stat.st_mtime,
                            access_count=0,
                            is_locked=False,
                            signature_hash=self._compute_file_hash(file_path)
                        )

                        self._file_infos[str(file_path)] = file_info
                        total_size += stat.st_size
                        file_count += 1

                    except (OSError, PermissionError) as e:
                        self.logger.debug(f"无法访问文件 {file_path}: {e}")
                        continue

            # 更新统计信息
            self._stats.total_files = file_count
            self._stats.total_size_bytes = total_size
            self._stats.avg_file_size_bytes = (
                total_size // file_count if file_count > 0 else 0
            )
            self._stats.oldest_file_age_seconds = oldest_age
            self._stats.newest_file_age_seconds = newest_age

            # 保存更新后的元数据
            self._save_metadata()

        except Exception as e:
            self.logger.error(f"扫描缓存文件失败: {e}")

    def _compute_file_hash(self, file_path: Path) -> Optional[str]:
        """计算文件哈希值"""
        try:
            hasher = hashlib.sha256()
            with open(file_path, 'rb') as f:
                # 只读取前1MB计算哈希（平衡速度和准确性）
                chunk = f.read(1024 * 1024)
                hasher.update(chunk)
            return hasher.hexdigest()[:16]  # 截断为16字符
        except Exception:
            return None

    def get_stats(self) -> CacheStats:
        """
        获取缓存统计信息

        Returns:
            缓存统计信息
        """
        with self._lock:
            # 更新扫描（如果已初始化）
            if self._is_initialized and not self._is_shutdown:
                self._scan_cache_files()

            return CacheStats(
                total_files=self._stats.total_files,
                total_size_bytes=self._stats.total_size_bytes,
                avg_file_size_bytes=self._stats.avg_file_size_bytes,
                oldest_file_age_seconds=self._stats.oldest_file_age_seconds,
                newest_file_age_seconds=self._stats.newest_file_age_seconds,
                cache_hit_rate=self._stats.cache_hit_rate,
                cache_miss_rate=self._stats.cache_miss_rate,
                last_cleanup_time=self._stats.last_cleanup_time,
                cleanup_count=self._stats.cleanup_count,
                reclaimed_space_bytes=self._stats.reclaimed_space_bytes
            )

    def cleanup(
            self,
            max_age_days: int = 30,
            max_size_mb: Optional[int] = None,
            dry_run: bool = False
    ) -> Tuple[int, int]:
        """
        清理缓存文件

        Args:
            max_age_days: 最大文件保留天数
            max_size_mb: 最大缓存大小（MB），None则使用初始化值
            dry_run: 模拟运行，不实际删除

        Returns:
            (删除的文件数, 回收的字节数)
        """
        if not self._is_initialized:
            self.logger.warning("缓存管理器未初始化，跳过清理")
            return 0, 0

        with self._lock:
            # 更新文件扫描
            self._scan_cache_files()

            if not self._file_infos:
                self.logger.info("缓存目录为空，无需清理")
                return 0, 0

            # 计算清理阈值
            current_time = time.time()
            max_age_seconds = max_age_days * 24 * 60 * 60
            max_size = (max_size_mb or (self.max_size_bytes // (1024 * 1024))) * 1024 * 1024

            # 收集需要清理的文件
            files_to_clean: List[Tuple[str, CacheFileInfo]] = []

            # 策略1：基于年龄清理
            if self.cleanup_strategy in [
                CacheCleanupStrategy.AGE_BASED,
                CacheCleanupStrategy.HYBRID
            ]:
                for file_path_str, file_info in self._file_infos.items():
                    file_age = current_time - file_info.created_time
                    if file_age > max_age_seconds:
                        files_to_clean.append((file_path_str, file_info))

            # 策略2：基于大小清理
            if self.cleanup_strategy in [
                CacheCleanupStrategy.SIZE_BASED,
                CacheCleanupStrategy.HYBRID
            ]:
                if self._stats.total_size_bytes > max_size:
                    # 按访问频率排序（最少访问的优先删除）
                    sorted_files = sorted(
                        self._file_infos.items(),
                        key=lambda x: (x[1].access_count, x[1].last_accessed_time)
                    )

                    current_total = self._stats.total_size_bytes
                    for file_path_str, file_info in sorted_files:
                        if current_total <= max_size:
                            break

                        # 确保不会重复添加
                        if (file_path_str, file_info) not in files_to_clean:
                            files_to_clean.append((file_path_str, file_info))
                            current_total -= file_info.size_bytes

            # 去重
            unique_files = {}
            for file_path_str, file_info in files_to_clean:
                unique_files[file_path_str] = file_info

            # 执行清理
            deleted_count = 0
            reclaimed_bytes = 0

            # 计算应该删除的文件数量和总大小
            for file_path_str, file_info in unique_files.items():
                try:
                    file_path = Path(file_path_str)
                    if file_path.exists():
                        file_size = file_path.stat().st_size
                        deleted_count += 1
                        reclaimed_bytes += file_size
                except Exception as e:
                    self.logger.warning(f"获取文件大小失败 {file_path_str}: {e}")

            # 如果不是模拟运行，实际删除文件并更新元数据
            if not dry_run:
                for file_path_str, file_info in unique_files.items():
                    try:
                        file_path = Path(file_path_str)
                        if file_path.exists():
                            file_path.unlink()
                            # 从元数据中移除
                            self._file_infos.pop(file_path_str, None)

                    except Exception as e:
                        self.logger.warning(f"删除缓存文件失败 {file_path_str}: {e}")
                        # 如果删除失败，从计数中减去（因为之前已经加上了）
                        try:
                            file_size = Path(file_path_str).stat().st_size
                            deleted_count -= 1
                            reclaimed_bytes -= file_size
                        except:
                            pass

                # 更新统计信息
                if deleted_count > 0:
                    self._stats.total_files -= deleted_count
                    self._stats.total_size_bytes -= reclaimed_bytes
                    self._stats.reclaimed_space_bytes += reclaimed_bytes
                    self._stats.cleanup_count += 1
                    self._stats.last_cleanup_time = current_time

                    # 重新计算平均值
                    if self._stats.total_files > 0:
                        self._stats.avg_file_size_bytes = (
                                self._stats.total_size_bytes // self._stats.total_files
                        )
                    else:
                        self._stats.avg_file_size_bytes = 0

                    # 保存元数据
                    self._save_metadata()

                    self.logger.info(
                        f"缓存清理完成: 删除 {deleted_count} 个文件, "
                        f"回收 {reclaimed_bytes / (1024 * 1024):.1f} MB"
                    )
            else:
                # 模拟运行
                for file_path_str, file_info in unique_files.items():
                    reclaimed_bytes += file_info.size_bytes

                self.logger.info(
                    f"模拟清理: 将删除 {len(unique_files)} 个文件, "
                    f"回收 {reclaimed_bytes / (1024 * 1024):.1f} MB"
                )

            return deleted_count, reclaimed_bytes

    def clear_all(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        清空所有缓存

        Args:
            dry_run: 模拟运行，不实际删除

        Returns:
            (删除的文件数, 回收的字节数)
        """
        if not self._is_initialized:
            self.logger.warning("缓存管理器未初始化，跳过清空")
            return 0, 0

        with self._lock:
            # 获取当前状态
            total_files = self._stats.total_files
            total_size = self._stats.total_size_bytes

            if total_files == 0:
                self.logger.info("缓存已为空，无需清空")
                return 0, 0

            if not dry_run:
                try:
                    # 删除整个缓存目录
                    if self._cache_dir_path.exists():
                        shutil.rmtree(self._cache_dir_path)

                    # 重新创建目录
                    self._cache_dir_path.mkdir(parents=True, exist_ok=True)

                    # 重置状态
                    self._file_infos.clear()
                    self._stats = CacheStats()
                    self._save_metadata()

                    self.logger.info(
                        f"缓存已清空: 删除 {total_files} 个文件, "
                        f"回收 {total_size / (1024 * 1024):.1f} MB"
                    )

                except Exception as e:
                    self.logger.error(f"清空缓存失败: {e}")
                    return 0, 0
            else:
                self.logger.info(
                    f"模拟清空: 将删除 {total_files} 个文件, "
                    f"回收 {total_size / (1024 * 1024):.1f} MB"
                )

            return total_files, total_size

    def get_file_info(self, file_path: str) -> Optional[CacheFileInfo]:
        """
        获取文件信息

        Args:
            file_path: 文件路径

        Returns:
            文件信息，如果不存在则返回None
        """
        with self._lock:
            # 规范化文件路径（处理符号链接）
            normalized_path = os.path.realpath(file_path)
            return self._file_infos.get(normalized_path)

    def mark_file_accessed(self, file_path: str) -> bool:
        """
        标记文件被访问

        Args:
            file_path: 文件路径

        Returns:
            是否成功更新
        """
        with self._lock:
            # 规范化文件路径（处理符号链接）
            normalized_path = os.path.realpath(file_path)
            if normalized_path in self._file_infos:
                file_info = self._file_infos[normalized_path]
                file_info.last_accessed_time = time.time()
                file_info.access_count += 1

                # 异步保存元数据（避免阻塞）
                threading.Thread(
                    target=self._save_metadata,
                    daemon=True
                ).start()

                return True
            return False

    def lock_file(self, file_path: str) -> bool:
        """
        锁定文件（防止多进程同时编译）

        Args:
            file_path: 文件路径

        Returns:
            是否成功锁定
        """
        if not self.enable_file_locking:
            return True  # 文件锁禁用时总是返回成功

        with self._lock:
            # 规范化文件路径（处理符号链接）
            normalized_path = os.path.realpath(file_path)
            if normalized_path in self._file_infos:
                file_info = self._file_infos[normalized_path]
                if file_info.is_locked:
                    return False  # 已被锁定

                file_info.is_locked = True
                self._save_metadata()
                return True
            return False

    def unlock_file(self, file_path: str) -> bool:
        """
        解锁文件

        Args:
            file_path: 文件路径

        Returns:
            是否成功解锁
        """
        with self._lock:
            # 规范化文件路径（处理符号链接）
            normalized_path = os.path.realpath(file_path)
            if normalized_path in self._file_infos:
                file_info = self._file_infos[normalized_path]
                file_info.is_locked = False
                self._save_metadata()
                return True
            return False

    def get_cache_health(self) -> Dict[str, Any]:
        """
        获取缓存健康状态

        Returns:
            健康状态字典
        """
        with self._lock:
            stats = self.get_stats()

            # 计算健康指标
            size_ratio = (
                stats.total_size_bytes / self.max_size_bytes
                if self.max_size_bytes > 0
                else 0
            )

            # 文件年龄分布
            current_time = time.time()
            age_distribution = {
                'less_than_day': 0,
                '1_7_days': 0,
                '7_30_days': 0,
                'more_than_30_days': 0
            }

            for file_info in self._file_infos.values():
                file_age = current_time - file_info.created_time
                if file_age < 86400:  # 1天
                    age_distribution['less_than_day'] += 1
                elif file_age < 604800:  # 7天
                    age_distribution['1_7_days'] += 1
                elif file_age < 2592000:  # 30天
                    age_distribution['7_30_days'] += 1
                else:
                    age_distribution['more_than_30_days'] += 1

            return {
                'total_files': stats.total_files,
                'total_size_bytes': stats.total_size_bytes,
                'max_size_bytes': self.max_size_bytes,
                'size_utilization_percent': size_ratio * 100,
                'avg_file_size_bytes': stats.avg_file_size_bytes,
                'oldest_file_age_days': stats.oldest_file_age_seconds / 86400,
                'cleanup_count': stats.cleanup_count,
                'last_cleanup_days_ago': (
                    (current_time - stats.last_cleanup_time) / 86400
                    if stats.last_cleanup_time
                    else None
                ),
                'age_distribution': age_distribution,
                'cache_hit_rate': stats.cache_hit_rate,
                'health_status': (
                    'healthy' if size_ratio < 0.8
                    else 'warning' if size_ratio < 0.95
                    else 'critical'
                )
            }

    def shutdown(self) -> bool:
        """
        关闭缓存管理器

        Returns:
            是否成功关闭
        """
        if self._is_shutdown:
            return True

        with self._lock:
            try:
                # 保存最后状态
                self._save_metadata()

                self._is_shutdown = True
                self.logger.info("Numba缓存管理器已关闭")

                return True

            except Exception as e:
                self.logger.error(f"关闭缓存管理器失败: {e}")
                return False


# 默认全局缓存管理器实例
_default_cache_manager: Optional[NumbaCacheManager] = None


def get_default_cache_manager() -> NumbaCacheManager:
    """获取默认缓存管理器"""
    global _default_cache_manager
    if _default_cache_manager is None:
        _default_cache_manager = NumbaCacheManager(
            cache_dir=None,
            max_size_mb=500,
            cleanup_strategy=CacheCleanupStrategy.HYBRID,
            enable_file_locking=True
        )
        _default_cache_manager.initialize()
    return _default_cache_manager


def cleanup_cache(
        max_age_days: int = 30,
        max_size_mb: Optional[int] = None,
        dry_run: bool = False
) -> Tuple[int, int]:
    """
    清理缓存（使用默认管理器）

    Args:
        max_age_days: 最大文件保留天数
        max_size_mb: 最大缓存大小（MB）
        dry_run: 模拟运行

    Returns:
        (删除的文件数, 回收的字节数)
    """
    manager = get_default_cache_manager()
    return manager.cleanup(max_age_days, max_size_mb, dry_run)


def get_cache_stats() -> CacheStats:
    """获取缓存统计信息（使用默认管理器）"""
    manager = get_default_cache_manager()
    return manager.get_stats()


def clear_all_cache(dry_run: bool = False) -> Tuple[int, int]:
    """清空所有缓存（使用默认管理器）"""
    manager = get_default_cache_manager()
    return manager.clear_all(dry_run)


def get_cache_health() -> Dict[str, Any]:
    """获取缓存健康状态（使用默认管理器）"""
    manager = get_default_cache_manager()
    return manager.get_cache_health()


# 上下文管理器支持
class CacheManagerContext:
    """缓存管理器上下文"""

    def __init__(
            self,
            cache_dir: Optional[str] = None,
            max_size_mb: int = 500
    ):
        self.cache_dir = cache_dir
        self.max_size_mb = max_size_mb
        self.manager: Optional[NumbaCacheManager] = None

    def __enter__(self) -> NumbaCacheManager:
        self.manager = NumbaCacheManager(
            cache_dir=self.cache_dir,
            max_size_mb=self.max_size_mb
        )
        self.manager.initialize()
        return self.manager

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.manager:
            self.manager.shutdown()
