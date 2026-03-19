"""
四号引擎v3.0 KDE引擎主控制器
集成进程池异步计算、LVN提取和实时监控
支持双核隔离架构，核心0处理I/O，核心1执行KDE计算
"""

import asyncio
import time
from typing import Dict, List, Optional, Tuple, Any, Callable
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import multiprocessing as mp
from dataclasses import asdict

from src.strategy.triplea.data_structures import (
    NormalizedTick, RangeBar, KDEEngineConfig, TripleAEngineConfig
)
from src.strategy.triplea.kde_core import KDECore
from src.strategy.triplea.kde_matrix import KDEMatrixEngine
from src.strategy.triplea.lvn_extractor import LVNExtractor, LVNRegion
from src.strategy.triplea.process_pool_manager import ProcessPoolManager
from src.utils.log import get_logger

logger = get_logger(__name__)


class KDEEngine:
    """
    KDE主引擎控制器
    负责协调KDE计算、LVN提取和进程池管理
    """

    def __init__(self, config: TripleAEngineConfig):
        """
        初始化KDE引擎

        Args:
            config: 四号引擎完整配置
        """
        self.config = config
        self.kde_config = config.kde_engine

        # 核心组件
        self.kde_core = KDECore(self.kde_config)
        self.kde_matrix = KDEMatrixEngine(self.kde_config)
        self.lvn_extractor = LVNExtractor(self.kde_config)

        # 进程池管理
        self.process_pool_manager: Optional[ProcessPoolManager] = None
        self.enable_cpu_affinity = config.enable_cpu_affinity
        # 数据缓冲区
        self.tick_buffer: List[NormalizedTick] = []
        self.price_history: List[float] = []
        self.active_lvn_regions: List[LVNRegion] = []

        # 状态跟踪
        self.stats = {
            'total_ticks_processed': 0,
            'kde_calculations': 0,
            'lvn_detections': 0,
            'avg_kde_time_ms': 0.0,
            'avg_lvn_extraction_time_ms': 0.0,
            'last_update_timestamp': 0
        }

        # 事件回调
        self.on_lvn_detected: Optional[Callable] = None
        self.on_kde_calculated: Optional[Callable] = None

        logger.info(f"KDEEngine初始化完成")

    async def start(self):
        """
        启动KDE引擎
        初始化进程池和必要组件
        """
        logger.info("🚀 启动KDE引擎...")

        # 检查是否启用CPU亲和性
        if self.enable_cpu_affinity:
            logger.info("🔧 启用CPU亲和性绑定")

        # 初始化进程池管理器
        self.process_pool_manager = ProcessPoolManager(
            max_workers=2,
            cpu_affinity=[1] if self.enable_cpu_affinity else None
        )

        # 启动进程池
        await self.process_pool_manager.start()

        # Numba预热
        if self.config.enable_background_warmup:
            await self._warmup_numba_functions()

        logger.info("✅ KDE引擎启动完成")

    async def stop(self):
        """
        停止KDE引擎
        清理进程池和缓冲区
        """
        logger.info("🛑 停止KDE引擎...")

        if self.process_pool_manager:
            await self.process_pool_manager.stop()

        # 清理缓冲区
        self.tick_buffer.clear()
        self.price_history.clear()
        self.active_lvn_regions.clear()

        logger.info("✅ KDE引擎停止完成")

    async def process_tick(self, tick: NormalizedTick) -> List[LVNRegion]:
        """
        处理单个Tick，触发KDE计算和LVN提取

        Args:
            tick: 标准化Tick

        Returns:
            LVN区域列表
        """
        start_time = time.perf_counter()

        try:
            # 将Tick添加到缓冲区
            self.tick_buffer.append(tick)
            self.price_history.append(tick.px)

            # 检查是否达到最小计算样本数
            if len(self.tick_buffer) < self.kde_config.min_slice_ticks:
                logger.debug(f"数据不足，等待更多Tick: {len(self.tick_buffer)}/{self.kde_config.min_slice_ticks}")
                return []

            # 提取价格数组
            prices = np.array([t.px for t in self.tick_buffer[-self.kde_config.min_slice_ticks:]])

            # 判断是否使用进程池
            if (self.process_pool_manager and
                self.config.enable_numba_cache and
                self.enable_cpu_affinity):

                # 使用进程池异步计算KDE
                grid, densities = await self._compute_kde_async(prices)
            else:
                # 直接计算KDE
                grid, densities = await self._compute_kde_direct(prices)

            if len(grid) == 0 or len(densities) == 0:
                return []

            # 触发KDE计算事件
            if self.on_kde_calculated:
                try:
                    self.on_kde_calculated(grid, densities)
                except Exception as e:
                    logger.warning(f"KDE计算事件回调失败: {e}")

            # 提取LVN区域
            lvn_regions = await self._extract_lvn_async(grid, densities)

            # 更新活动区域
            self.active_lvn_regions = lvn_regions

            # 触发LVN检测事件
            if self.on_lvn_detected and lvn_regions:
                try:
                    self.on_lvn_detected(lvn_regions)
                except Exception as e:
                    logger.warning(f"LVN检测事件回调失败: {e}")

            # 更新统计信息
            self._update_stats(start_time, len(lvn_regions))

            return lvn_regions

        except Exception as e:
            logger.error(f"处理Tick时出错: {e}")
            return []

    async def _compute_kde_async(self, prices: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        使用进程池异步计算KDE

        Args:
            prices: 价格数组

        Returns:
            (网格点, 密度估计)
        """
        if not self.process_pool_manager:
            return np.array([]), np.array([])

        try:
            # 准备任务数据
            task_data = {
                'prices': prices,
                'bandwidth_method': self.kde_config.bandwidth_method,
                'min_slice_ticks': self.kde_config.min_slice_ticks
            }

            # 提交KDE计算任务到进程池
            result = await self.process_pool_manager.submit_task(
                'compute_kde',
                task_data
            )

            if result and 'success' in result and result['success']:
                grid = np.array(result['grid'])
                densities = np.array(result['densities'])
                return grid, densities
            else:
                logger.warning("异步KDE计算失败，回退到直接计算")
                return await self._compute_kde_direct(prices)

        except Exception as e:
            logger.warning(f"异步KDE计算异常: {e}，回退到直接计算")
            return await self._compute_kde_direct(prices)

    async def _compute_kde_direct(self, prices: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        直接计算KDE（不使用进程池）

        Args:
            prices: 价格数组

        Returns:
            (网格点, 密度估计)
        """
        try:
            grid, densities = self.kde_core.compute_kde(prices)
            return grid, densities
        except Exception as e:
            logger.error(f"直接KDE计算失败: {e}")
            return np.array([]), np.array([])

    async def _extract_lvn_async(
        self,
        grid: np.ndarray,
        densities: np.ndarray
    ) -> List[LVNRegion]:
        """
        异步提取LVN区域

        Args:
            grid: 网格点数组
            densities: 密度数组

        Returns:
            LVN区域列表
        """
        try:
            # 直接提取LVN区域
            lvn_regions = self.lvn_extractor.extract_from_kde(grid, densities)

            # 过滤和合并重叠区域
            filtered_regions = self.lvn_extractor.filter_and_merge_regions(
                lvn_regions,
                price_tolerance=0.5
            )

            return filtered_regions

        except Exception as e:
            logger.error(f"LVN提取失败: {e}")
            return []

    async def _warmup_numba_functions(self):
        """
        预热Numba JIT编译的函数
        减少实时计算的延迟
        """
        logger.info("🔥 预热Numba JIT函数...")

        # 创建测试数据
        test_prices = np.random.randn(1000) * 50 + 3000
        test_grid = np.linspace(2900, 3100, 100)

        # 预热KDE核心函数
        try:
            from src.strategy.triplea.kde_core import (
                kde_density_1d,
                silverman_bandwidth,
                find_local_minima,
                find_local_maxima
            )

            # 执行预热计算
            bandwidth = silverman_bandwidth(test_prices)
            densities = kde_density_1d(test_prices, test_grid, bandwidth)
            minima = find_local_minima(test_grid, densities)
            maxima = find_local_maxima(test_grid, densities)

            logger.info(f"✅ Numba预热完成: {len(densities)}密度点, {len(minima)}最小值, {len(maxima)}最大值")

        except Exception as e:
            logger.warning(f"Numba预热异常: {e}")

    def _update_stats(self, start_time: float, lvn_count: int):
        """
        更新引擎统计信息

        Args:
            start_time: 处理开始时间
            lvn_count: 检测到的LVN区域数
        """
        processing_time_ms = (time.perf_counter() - start_time) * 1000

        # 更新统计信息
        self.stats['total_ticks_processed'] += 1
        self.stats['kde_calculations'] += 1
        self.stats['lvn_detections'] += lvn_count

        # 更新平均时间
        prev_avg_kde = self.stats['avg_kde_time_ms']
        n_kde = self.stats['kde_calculations']

        # 指数移动平均

        self.stats['avg_kde_time_ms'] = \
            (prev_avg_kde * (n_kde - 1) + processing_time_ms) / n_kde if n_kde > 0 else processing_time_ms

        self.stats['last_update_timestamp'] = time.time()

    def get_active_lvn_regions(self) -> List[LVNRegion]:
        """
        获取当前活动的LVN区域

        Returns:
            活动LVN区域列表
        """
        return self.active_lvn_regions.copy()

    def get_lvn_regions_near_price(
        self,
        price: float,
        max_distance: float = 10.0
    ) -> List[LVNRegion]:
        """
        获取指定价格附近的LVN区域

        Args:
            price: 目标价格
            max_distance: 最大距离限制

        Returns:
            附近的LVN区域列表
        """
        if not self.active_lvn_regions:
            return []

        near_regions = []

        for region in self.active_lvn_regions:
            if region.contains_price(price):
                near_regions.append(region)
            else:
                distance = region.distance_to_center(price)
                if distance <= max_distance:
                    near_regions.append(region)

        return near_regions

    def get_stats(self) -> Dict[str, Any]:
        """
        获取引擎统计信息

        Returns:
            统计信息字典
        """
        stats_copy = self.stats.copy()

        # 添加额外信息
        stats_copy['buffer_size'] = len(self.tick_buffer)
        stats_copy['price_history_size'] = len(self.price_history)
        stats_copy['active_lvn_regions_count'] = len(self.active_lvn_regions)

        # 添加配置信息
        stats_copy['config'] = {
            'min_slice_ticks': self.kde_config.min_slice_ticks,
            'lvn_density_percentile': self.kde_config.lvn_density_percentile,
            'bandwidth_method': self.kde_config.bandwidth_method
        }

        return stats_copy

    def reset(self):
        """
        重置引擎状态
        """
        logger.info("🔄 重置KDE引擎")

        # 清理缓冲区
        self.tick_buffer.clear()
        self.price_history.clear()
        self.active_lvn_regions.clear()

        # 重置统计信息
        self.stats = {
            'total_ticks_processed': 0,
            'kde_calculations': 0,
            'lvn_detections': 0,
            'avg_kde_time_ms': 0.0,
            'avg_lvn_extraction_time_ms': 0.0,
            'last_update_timestamp': time.time()
        }

        logger.info("✅ KDE引擎重置完成")


# 测试函数
async def test_kde_engine_performance():
    """
    测试KDE引擎性能
    """
    import asyncio

    logger = get_logger(__name__)

    # 创建配置
    config = TripleAEngineConfig()

    # 修改配置为测试参数
    config.kde_engine.min_slice_ticks = 100
    config.enable_numba_cache = False  # 禁用Numba缓存，避免异步计算问题
    config.enable_background_warmup = True
    config.enable_cpu_affinity = False  # 禁用CPU亲和性，避免进程池问题

    # 创建引擎实例

    engine = KDEEngine(config)

    logger.info("🔬 KDE引擎性能测试开始")
    logger.info(f"配置: {config}")

    # 启动引擎

    await engine.start()

    # 创建测试数据

    n_ticks = 1000
    test_ticks = []

    base_price = 3000.0
    volatility = 50.0

    for i in range(n_ticks):

        price = base_price + np.random.randn() * volatility
        size = np.random.uniform(0.1, 5.0)
        side = 1 if np.random.rand() > 0.5 else -1

        tick = NormalizedTick(

            ts=int(time.time() * 1e9) + i * 1000000,  # 模拟1ms间隔
            px=price,
            sz=size,
            side=side
        )
        test_ticks.append(tick)

    # 性能测试

    processing_times = []

    logger.info(f"处理 {n_ticks} 个Tick...")

    start_total_time = time.perf_counter()

    # 设置回调函数

    def on_kde_calculated(grid, densities):

        logger.debug(f"KDE计算完成: 网格大小={len(grid)}, 密度范围=[{np.min(densities):.2e}, {np.max(densities):.2e}]")

    def on_lvn_detected(regions):

        logger.debug(f"检测到 {len(regions)} 个LVN区域")

    # 注册回调

    engine.on_kde_calculated = on_kde_calculated
    engine.on_lvn_detected = on_lvn_detected

    # 模拟实时处理

    for i, tick in enumerate(test_ticks):

        tick_start = time.perf_counter()

        # 处理Tick

        lvn_regions = await engine.process_tick(tick)

        tick_time = time.perf_counter() - tick_start
        processing_times.append(tick_time * 1000)  # 转换为ms

        # 输出进度

        if (i + 1) % 100 == 0:

            avg_time = np.mean(processing_times[-100:])
            logger.info(f"  已处理 {i + 1} 个Tick, 平均延迟: {avg_time:.2f}ms")

            if lvn_regions:

                logger.info(f"    检测到 {len(lvn_regions)} 个LVN区域")

    total_time = time.perf_counter() - start_total_time

    # 计算统计信息

    avg_processing_time = np.mean(processing_times)
    p50_processing_time = np.percentile(processing_times, 50)
    p95_processing_time = np.percentile(processing_times, 95)
    p99_processing_time = np.percentile(processing_times, 99)

    # 输出性能报告

    logger.info("\n📊 KDE引擎性能报告")

    logger.info(f"  总处理时间: {total_time:.3f}s")

    logger.info(f"  平均单Tick延迟: {avg_processing_time:.2f}ms")

    logger.info(f"  P50延迟: {p50_processing_time:.2f}ms")

    logger.info(f"  P95延迟: {p95_processing_time:.2f}ms")

    logger.info(f"  P99延迟: {p99_processing_time:.2f}ms")

    logger.info(f"  总Tick数: {n_ticks}")

    logger.info(f"  KDE计算次数: {engine.stats['kde_calculations']}")

    logger.info(f"  LVN检测次数: {engine.stats['lvn_detections']}")

    # 输出引擎统计信息

    stats = engine.get_stats()

    logger.info(f"  当前缓冲区大小: {stats['buffer_size']}")

    logger.info(f"  价格历史长度: {stats['price_history_size']}")

    logger.info(f"  活动LVN区域数: {stats['active_lvn_regions_count']}")

    # 性能断言

    target_latency = 0.5  # 0.5ms目标延迟

    if avg_processing_time < target_latency:

        logger.info(f"✅ 性能测试通过: 平均延迟 {avg_processing_time:.2f}ms < {target_latency}ms 目标")

    else:

        logger.warning(f"⚠️  性能测试未通过: 平均延迟 {avg_processing_time:.2f}ms ≥ {target_latency}ms 目标")

    # 停止引擎

    await engine.stop()

    return avg_processing_time, stats


if __name__ == "__main__":

    # 运行性能测试

    asyncio.run(test_kde_engine_performance())