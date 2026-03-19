"""
四号引擎v3.0 Numba JIT预热管理器
解决Numba冷启动问题（200-500ms编译延迟），提供预热策略和缓存管理
"""

import asyncio
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar
import warnings

# 导入现有日志模块
from src.utils.log import get_logger

import numpy as np

# 尝试导入numba，如果不可用则提供降级方案
try:
    from numba import njit, jit, vectorize, guvectorize
    from numba.core.dispatcher import Dispatcher
    from numba.core.caching import Cache
    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False
    # 创建虚拟装饰器用于降级模式
    class Dispatcher:
        """Numba不可用时的虚拟分发器"""
        pass

    def njit(*args, **kwargs):
        """虚拟njit装饰器"""
        def decorator(func):
            return func
        return decorator

    def jit(*args, **kwargs):
        """虚拟jit装饰器"""
        def decorator(func):
            return func
        return decorator

    def vectorize(*args, **kwargs):
        """虚拟vectorize装饰器"""
        def decorator(func):
            return func
        return decorator

    def guvectorize(*args, **kwargs):
        """虚拟guvectorize装饰器"""
        def decorator(func):
            return func
        return decorator


# 类型变量
F = TypeVar('F', bound=Callable)


class WarmupStrategy(Enum):
    """预热策略枚举"""
    EAGER = "eager"      # 急切预热：启动时立即编译所有函数
    LAZY = "lazy"        # 懒预热：首次使用时编译
    BACKGROUND = "background"  # 后台预热：启动后在后台线程编译
    HYBRID = "hybrid"    # 混合策略：关键函数急切，其他函数后台预热


@dataclass
class JITFunctionInfo:
    """JIT函数信息"""
    name: str
    func: Callable
    signature: Optional[str] = None
    compile_time: float = 0.0
    last_used: float = field(default_factory=time.time)
    call_count: int = 0
    is_compiled: bool = False
    is_critical: bool = False  # 是否为关键函数（需要急切预热）
    warmup_data: Optional[List[Tuple]] = None  # 预热数据样本


@dataclass
class WarmupStats:
    """预热统计信息"""
    total_functions: int = 0
    compiled_functions: int = 0
    total_compile_time: float = 0.0
    avg_compile_time: float = 0.0
    max_compile_time: float = 0.0
    min_compile_time: float = float('inf')
    cache_hits: int = 0
    cache_misses: int = 0
    background_tasks: int = 0
    background_errors: int = 0


class NumbaWarmupManager:
    """
    Numba JIT预热管理器

    主要功能：
    1. 管理Numba函数的预热编译，解决冷启动延迟问题
    2. 提供多种预热策略（急切、懒、后台、混合）
    3. 监控编译性能和缓存命中率
    4. 提供降级模式（当Numba不可用时）
    5. 生成预热数据样本用于编译

    使用示例：
    ```python
    # 创建预热管理器
    warmup_manager = NumbaWarmupManager(strategy=WarmupStrategy.HYBRID)

    # 注册需要预热的函数
    @warmup_manager.register(critical=True)
    @njit(cache=True)
    def kde_core(prices, bandwidth):
        # KDE核心计算
        pass

    # 启动预热
    await warmup_manager.warmup()

    # 使用函数（已预热）
    result = kde_core(prices_array, 0.5)
    ```
    """

    def __init__(
        self,
        strategy: WarmupStrategy = WarmupStrategy.HYBRID,
        enable_background_warmup: bool = True,
        background_threads: int = 2,
        warmup_data_size: int = 100,
        logger: Optional[Any] = None
    ):
        """
        初始化预热管理器

        Args:
            strategy: 预热策略
            enable_background_warmup: 是否启用后台预热
            background_threads: 后台预热线程数
            warmup_data_size: 预热数据样本大小
            logger: 日志记录器
        """
        self.strategy = strategy
        self.enable_background_warmup = enable_background_warmup
        self.background_threads = background_threads
        self.warmup_data_size = warmup_data_size

        self.logger = logger or get_logger(__name__)
        self._functions: Dict[str, JITFunctionInfo] = {}
        self._stats = WarmupStats()
        self._background_executor: Optional[ThreadPoolExecutor] = None
        self._lock = threading.RLock()
        self._is_warming_up = False
        self._is_shutdown = False

        # 检查Numba可用性
        if not NUMBA_AVAILABLE:
            self.logger.warning(
                "Numba不可用，启用降级模式。JIT编译将被跳过，性能可能受影响。"
            )

    def register(
        self,
        critical: bool = False,
        warmup_data_generator: Optional[Callable[[int], Tuple]] = None,
        signature: Optional[str] = None
    ) -> Callable[[F], F]:
        """
        注册JIT函数装饰器

        Args:
            critical: 是否为关键函数（需要急切预热）
            warmup_data_generator: 预热数据生成器，返回函数参数元组
            signature: 函数签名（用于Numba编译）

        Returns:
            装饰器函数
        """
        def decorator(func: F) -> F:
            func_name = func.__name__

            with self._lock:
                if func_name in self._functions:
                    self.logger.warning(f"函数 {func_name} 已注册，将被覆盖")

                # 创建函数信息
                func_info = JITFunctionInfo(
                    name=func_name,
                    func=func,
                    signature=signature,
                    is_critical=critical,
                    warmup_data=self._generate_warmup_data(func, warmup_data_generator)
                )

                self._functions[func_name] = func_info
                self._stats.total_functions += 1

            self.logger.debug(f"注册JIT函数: {func_name} (critical={critical})")
            return func

        return decorator

    def _generate_warmup_data(
        self,
        func: Callable,
        data_generator: Optional[Callable[[int], Tuple]]
    ) -> Optional[List[Tuple]]:
        """
        生成预热数据样本

        Args:
            func: 目标函数
            data_generator: 数据生成器

        Returns:
            预热数据样本列表
        """
        if not self.warmup_data_size or self.warmup_data_size <= 0:
            return None

        if data_generator:
            try:
                return [data_generator(self.warmup_data_size) for _ in range(3)]
            except Exception as e:
                self.logger.warning(f"预热数据生成失败: {e}")
                return None

        # 默认数据生成器：基于函数参数类型生成样本数据
        # 这里提供一些常见类型的默认生成器
        return None

    async def warmup(self, timeout: float = 30.0) -> bool:
        """
        执行预热编译

        Args:
            timeout: 预热超时时间（秒）

        Returns:
            是否成功完成预热
        """
        if self._is_warming_up:
            self.logger.warning("预热已在进行中")
            return False

        if self._is_shutdown:
            self.logger.error("管理器已关闭，无法预热")
            return False

        self._is_warming_up = True
        start_time = time.time()

        try:
            self.logger.info(f"开始Numba JIT预热 (策略: {self.strategy.value})")

            if not NUMBA_AVAILABLE:
                self.logger.info("Numba不可用，跳过预热")
                return True

            # 根据策略执行预热
            if self.strategy == WarmupStrategy.EAGER:
                success = await self._eager_warmup(timeout)
            elif self.strategy == WarmupStrategy.LAZY:
                success = True  # 懒预热不立即编译
            elif self.strategy == WarmupStrategy.BACKGROUND:
                success = await self._background_warmup(timeout)
            elif self.strategy == WarmupStrategy.HYBRID:
                success = await self._hybrid_warmup(timeout)
            else:
                self.logger.error(f"未知预热策略: {self.strategy}")
                success = False

            # 更新统计信息
            elapsed = time.time() - start_time
            self.logger.info(
                f"预热完成: 编译 {self._stats.compiled_functions}/"
                f"{self._stats.total_functions} 个函数, "
                f"耗时 {elapsed:.2f}秒"
            )

            return success

        except Exception as e:
            self.logger.error(f"预热过程中发生错误: {e}", exc_info=True)
            return False
        finally:
            self._is_warming_up = False

    async def _eager_warmup(self, timeout: float) -> bool:
        """急切预热：立即编译所有函数"""
        self.logger.info("执行急切预热策略")

        tasks = []
        for func_info in self._functions.values():
            if self._should_compile_now(func_info):
                task = asyncio.create_task(self._compile_function(func_info))
                tasks.append(task)

        if not tasks:
            return True

        # 等待所有编译任务完成
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self.logger.warning(f"预热超时 ({timeout}秒)")
            return False
        except Exception as e:
            self.logger.error(f"急切预热失败: {e}")
            return False

    async def _background_warmup(self, timeout: float) -> bool:
        """后台预热：在后台线程中编译"""
        self.logger.info("执行后台预热策略")

        if not self.enable_background_warmup:
            self.logger.warning("后台预热已禁用，回退到懒预热")
            return True

        # 启动后台执行器
        self._background_executor = ThreadPoolExecutor(
            max_workers=self.background_threads,
            thread_name_prefix="numba_warmup_"
        )

        # 提交后台编译任务
        for func_info in self._functions.values():
            if self._should_compile_now(func_info):
                self._background_executor.submit(self._compile_in_background, func_info)
                self._stats.background_tasks += 1

        self.logger.info(f"已提交 {self._stats.background_tasks} 个后台编译任务")
        return True

    async def _hybrid_warmup(self, timeout: float) -> bool:
        """混合预热：关键函数急切，其他函数后台"""
        self.logger.info("执行混合预热策略")

        # 急切编译关键函数
        critical_tasks = []
        for func_info in self._functions.values():
            if func_info.is_critical and self._should_compile_now(func_info):
                task = asyncio.create_task(self._compile_function(func_info))
                critical_tasks.append(task)

        # 等待关键函数编译完成
        if critical_tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*critical_tasks), timeout=timeout/2)
            except asyncio.TimeoutError:
                self.logger.warning("关键函数编译超时")
            except Exception as e:
                self.logger.error(f"关键函数编译失败: {e}")

        # 后台编译非关键函数
        if self.enable_background_warmup:
            self._background_executor = ThreadPoolExecutor(
                max_workers=self.background_threads,
                thread_name_prefix="numba_warmup_"
            )

            for func_info in self._functions.values():
                if not func_info.is_critical and self._should_compile_now(func_info):
                    self._background_executor.submit(
                        self._compile_in_background, func_info
                    )
                    self._stats.background_tasks += 1

            self.logger.info(f"已提交 {self._stats.background_tasks} 个后台编译任务")

        return True

    def _should_compile_now(self, func_info: JITFunctionInfo) -> bool:
        """判断是否应该立即编译函数"""
        if not NUMBA_AVAILABLE:
            return False

        if func_info.is_compiled:
            return False

        # 检查是否是Numba分发器
        if not hasattr(func_info.func, 'compile'):
            return False

        return True

    async def _compile_function(self, func_info: JITFunctionInfo) -> bool:
        """编译单个函数"""
        try:
            self.logger.debug(f"编译函数: {func_info.name}")
            compile_start = time.time()

            # 使用预热数据编译
            if func_info.warmup_data:
                for warmup_args in func_info.warmup_data:
                    try:
                        func_info.func(*warmup_args)
                    except Exception:
                        pass  # 忽略编译错误

            # 编译函数
            if hasattr(func_info.func, 'compile'):
                if func_info.signature:
                    func_info.func.compile(func_info.signature)
                else:
                    # 如果没有提供签名，尝试编译默认签名
                    # 一些Numba版本可能需要签名参数
                    try:
                        func_info.func.compile()
                    except TypeError as e:
                        if "missing 1 required positional argument: 'sig'" in str(e):
                            self.logger.warning(
                                f"函数 {func_info.name} 需要签名参数，通过预热数据调用编译"
                            )
                            # 继续执行，编译时间将通过预热数据调用测量
                        else:
                            raise

            compile_time = time.time() - compile_start
            func_info.compile_time = compile_time
            func_info.is_compiled = True

            # 更新统计信息
            with self._lock:
                self._stats.compiled_functions += 1
                self._stats.total_compile_time += compile_time
                self._stats.avg_compile_time = (
                    self._stats.total_compile_time / self._stats.compiled_functions
                )
                self._stats.max_compile_time = max(
                    self._stats.max_compile_time, compile_time
                )
                self._stats.min_compile_time = min(
                    self._stats.min_compile_time, compile_time
                )

            self.logger.debug(
                f"函数 {func_info.name} 编译完成, 耗时 {compile_time*1000:.1f}ms"
            )
            return True

        except Exception as e:
            self.logger.warning(f"函数 {func_info.name} 编译失败: {e}")
            return False

    def _compile_in_background(self, func_info: JITFunctionInfo) -> None:
        """在后台线程中编译函数"""
        try:
            # 在后台线程中运行同步编译
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            success = loop.run_until_complete(self._compile_function(func_info))
            if not success:
                self._stats.background_errors += 1

            loop.close()
        except Exception as e:
            self.logger.error(f"后台编译失败: {e}")
            self._stats.background_errors += 1

    def get_stats(self) -> WarmupStats:
        """获取预热统计信息"""
        with self._lock:
            return WarmupStats(
                total_functions=self._stats.total_functions,
                compiled_functions=self._stats.compiled_functions,
                total_compile_time=self._stats.total_compile_time,
                avg_compile_time=self._stats.avg_compile_time,
                max_compile_time=self._stats.max_compile_time,
                min_compile_time=self._stats.min_compile_time,
                cache_hits=self._stats.cache_hits,
                cache_misses=self._stats.cache_misses,
                background_tasks=self._stats.background_tasks,
                background_errors=self._stats.background_errors
            )

    def get_function_info(self, func_name: str) -> Optional[JITFunctionInfo]:
        """获取函数信息"""
        with self._lock:
            return self._functions.get(func_name)

    def mark_function_used(self, func_name: str) -> None:
        """标记函数被使用"""
        with self._lock:
            if func_name in self._functions:
                self._functions[func_name].last_used = time.time()
                self._functions[func_name].call_count += 1

    async def shutdown(self) -> None:
        """关闭管理器"""
        if self._is_shutdown:
            return

        self._is_shutdown = True

        # 关闭后台执行器
        if self._background_executor:
            self._background_executor.shutdown(wait=False)
            self._background_executor = None

        self.logger.info("Numba预热管理器已关闭")


# 默认全局管理器实例
_default_manager: Optional[NumbaWarmupManager] = None


def get_default_warmup_manager() -> NumbaWarmupManager:
    """获取默认预热管理器"""
    global _default_manager
    if _default_manager is None:
        _default_manager = NumbaWarmupManager(
            strategy=WarmupStrategy.HYBRID,
            enable_background_warmup=True,
            background_threads=2
        )
    return _default_manager


def register_jit_function(
    critical: bool = False,
    warmup_data_generator: Optional[Callable[[int], Tuple]] = None,
    signature: Optional[str] = None
) -> Callable[[F], F]:
    """
    注册JIT函数的便捷装饰器（使用默认管理器）

    Args:
        critical: 是否为关键函数
        warmup_data_generator: 预热数据生成器
        signature: 函数签名

    Returns:
        装饰器函数
    """
    manager = get_default_warmup_manager()
    return manager.register(critical, warmup_data_generator, signature)


async def warmup_all(timeout: float = 30.0) -> bool:
    """
    预热所有已注册函数（使用默认管理器）

    Args:
        timeout: 预热超时时间

    Returns:
        是否成功
    """
    manager = get_default_warmup_manager()
    return await manager.warmup(timeout)


def get_warmup_stats() -> WarmupStats:
    """获取预热统计信息（使用默认管理器）"""
    manager = get_default_warmup_manager()
    return manager.get_stats()


# 预定义的常用函数装饰器
def critical_jit(*args, **kwargs):
    """关键JIT函数装饰器"""
    if 'cache' not in kwargs:
        kwargs['cache'] = True

    def decorator(func):
        # 先应用njit装饰器
        jitted_func = njit(*args, **kwargs)(func)
        # 然后注册到管理器
        registered_func = register_jit_function(critical=True)(jitted_func)
        return registered_func
    return decorator


def background_jit(*args, **kwargs):
    """后台JIT函数装饰器"""
    if 'cache' not in kwargs:
        kwargs['cache'] = True

    def decorator(func):
        # 先应用njit装饰器
        jitted_func = njit(*args, **kwargs)(func)
        # 然后注册到管理器
        registered_func = register_jit_function(critical=False)(jitted_func)
        return registered_func
    return decorator