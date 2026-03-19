"""
四号引擎v3.0 CVD计算器
累积成交量差值（Cumulative Volume Delta）计算引擎
使用Numpy矩阵广播实现高性能滑动窗口计算
专为实时Tick流处理优化，毫秒级延迟
"""

from typing import List, Dict, Optional, Tuple, Deque
from collections import deque
import numpy as np
from numba import njit

from src.strategy.triplea.data_structures import NormalizedTick
from src.utils.log import get_logger

logger = get_logger(__name__)


class CVDCalculator:
    """
    CVD计算器（累积成交量差值）

    实时计算滑动窗口内的CVD，支持多时间窗口分析
    使用增量更新算法优化性能
    """

    def __init__(self, window_sizes: List[int] = None, max_history: int = 1000):
        """
        初始化CVD计算器

        Args:
            window_sizes: 窗口大小列表（以Tick数为单位）
            max_history: 最大历史记录长度
        """
        if window_sizes is None:
            window_sizes = [10, 30, 60, 120, 240]  # 多时间窗口分析

        self.window_sizes = window_sizes
        self.max_history = max_history

        # 数据缓冲区
        self.tick_buffer: Deque[NormalizedTick] = deque(maxlen=max_history)

        # 每个窗口的Tick队列和当前CVD值（增量更新）
        self.window_queues: Dict[int, Deque[NormalizedTick]] = {}
        self.window_cvd: Dict[int, float] = {}

        for window in window_sizes:
            self.window_queues[window] = deque()
            self.window_cvd[window] = 0.0

        # CVD历史记录：每个窗口大小对应一个CVD历史队列
        self.cvd_history: Dict[int, Deque[float]] = {
            window: deque(maxlen=max_history) for window in window_sizes
        }

        # 统计特征缓存
        self.cvd_stats: Dict[int, Dict[str, float]] = {
            window: {'mean': 0.0, 'std': 0.0, 'z_score': 0.0}
            for window in window_sizes
        }

        # 性能统计
        self.stats = {
            'ticks_processed': 0,
            'cvd_updates': 0,
            'total_processing_time_ns': 0
        }

        # 统计更新频率控制（每10个tick更新一次统计）
        self.stats_update_counter = 0
        self.stats_update_interval = 10  # 每10个tick更新一次统计

        logger.info(f"CVDCalculator初始化完成，窗口大小: {window_sizes}")

    def on_tick(self, tick: NormalizedTick) -> Dict[int, float]:
        """
        处理单个Tick，更新所有窗口的CVD

        Args:
            tick: 标准化Tick

        Returns:
            当前CVD值字典 {窗口大小: CVD值}
        """
        import time
        start_time = time.perf_counter_ns()

        try:
            # 将Tick添加到缓冲区
            self.tick_buffer.append(tick)

            # 更新所有窗口的CVD（增量更新）
            updated_cvd = {}
            for window in self.window_sizes:
                cvd_value = self._update_window_cvd(window, tick)
                updated_cvd[window] = cvd_value

            # 更新性能统计
            self.stats['ticks_processed'] += 1
            self.stats['cvd_updates'] += len(self.window_sizes)

            # 控制统计特征更新频率（每10个tick更新一次）
            self.stats_update_counter += 1
            if self.stats_update_counter >= self.stats_update_interval:
                self._update_statistics()
                self.stats_update_counter = 0

            return updated_cvd

        finally:
            end_time = time.perf_counter_ns()
            self.stats['total_processing_time_ns'] += (end_time - start_time)

    def _update_window_cvd(self, window: int, tick: NormalizedTick) -> float:
        """
        增量更新指定窗口的CVD

        Args:
            window: 窗口大小
            tick: 新到达的Tick

        Returns:
            更新后的CVD值
        """
        queue = self.window_queues[window]
        current_cvd = self.window_cvd[window]

        # 计算新Tick的贡献
        tick_contribution = tick.sz if tick.side == 1 else -tick.sz

        # 将新Tick加入队列
        queue.append(tick)

        # 更新CVD：加上新Tick的贡献
        current_cvd += tick_contribution

        # 如果队列大小超过窗口，移除最旧的Tick并减去其贡献
        if len(queue) > window:
            old_tick = queue.popleft()
            old_contribution = old_tick.sz if old_tick.side == 1 else -old_tick.sz
            current_cvd -= old_contribution

        # 更新当前CVD值
        self.window_cvd[window] = current_cvd
        self.cvd_history[window].append(current_cvd)

        return current_cvd

    def _update_statistics(self, window: Optional[int] = None):
        """更新CVD统计特征（均值、标准差、Z-score）

        Args:
            window: 指定窗口大小，None表示更新所有窗口
        """
        if window is not None:
            windows = [window]
        else:
            windows = self.window_sizes

        for window in windows:
            history = list(self.cvd_history[window])
            if len(history) < 2:
                continue

            # 计算均值和标准差
            mean_val = np.mean(history)
            std_val = np.std(history, ddof=1) if len(history) > 1 else 0.0

            # 计算当前Z-score
            current_val = self.window_cvd[window]
            z_score = (current_val - mean_val) / std_val if std_val > 0 else 0.0

            self.cvd_stats[window] = {
                'mean': float(mean_val),
                'std': float(std_val),
                'z_score': float(z_score)
            }

    def get_current_cvd(self, window: Optional[int] = None) -> Dict[int, float]:
        """
        获取当前CVD值

        Args:
            window: 指定窗口大小，None表示获取所有窗口

        Returns:
            CVD值字典
        """
        if window is not None:
            return {window: self.window_cvd[window]}
        return self.window_cvd.copy()

    def get_statistics(self, window: Optional[int] = None) -> Dict[int, Dict[str, float]]:
        """
        获取CVD统计特征

        Args:
            window: 指定窗口大小，None表示获取所有窗口

        Returns:
            统计特征字典
        """
        # 确保统计信息是最新的
        self._update_statistics(window)

        if window is not None:
            return {window: self.cvd_stats[window]}
        return self.cvd_stats.copy()

    def get_history(self, window: int, n_points: Optional[int] = None) -> List[float]:
        """
        获取CVD历史记录

        Args:
            window: 窗口大小
            n_points: 历史点数，None表示获取所有

        Returns:
            CVD历史列表（最新的在前）
        """
        history = list(self.cvd_history[window])
        if n_points is not None:
            return history[-n_points:]
        return history

    def reset(self):
        """重置计算器状态"""
        self.tick_buffer.clear()
        for window in self.window_sizes:
            self.window_queues[window].clear()
            self.window_cvd[window] = 0.0
            self.cvd_history[window].clear()
            self.cvd_stats[window] = {'mean': 0.0, 'std': 0.0, 'z_score': 0.0}

        self.stats = {
            'ticks_processed': 0,
            'cvd_updates': 0,
            'total_processing_time_ns': 0
        }

        logger.info("CVDCalculator已重置")

    def get_stats(self) -> dict:
        """获取性能统计"""
        return self.stats.copy()


class BatchCVDCalculator:
    """
    批量CVD计算器（Numpy加速版本）

    使用Numpy矩阵广播实现高性能批量CVD计算
    """

    def __init__(self, window_sizes: List[int] = None):
        """
        初始化批量CVD计算器

        Args:
            window_sizes: 窗口大小列表
        """
        if window_sizes is None:
            window_sizes = [10, 30, 60, 120, 240]

        self.window_sizes = window_sizes

        # Numpy数组缓冲区
        self.buffer_size = 10000  # 预分配缓冲区大小
        self.price_buffer = np.zeros(self.buffer_size, dtype=np.float64)
        self.size_buffer = np.zeros(self.buffer_size, dtype=np.float64)
        self.side_buffer = np.zeros(self.buffer_size, dtype=np.int8)  # 1=买入, -1=卖出
        self.buffer_idx = 0

        logger.info(f"BatchCVDCalculator初始化完成，窗口大小: {window_sizes}")

    def add_ticks(self, ticks: List[NormalizedTick]) -> Dict[int, np.ndarray]:
        """
        批量添加Tick并计算CVD

        Args:
            ticks: Tick列表

        Returns:
            字典 {窗口大小: CVD数组}
        """
        if not ticks:
            return {}

        # 将Tick数据添加到缓冲区
        for tick in ticks:
            if self.buffer_idx >= self.buffer_size:
                # 缓冲区满，重新分配
                self._resize_buffer()

            self.price_buffer[self.buffer_idx] = tick.px
            self.size_buffer[self.buffer_idx] = tick.sz
            self.side_buffer[self.buffer_idx] = tick.side
            self.buffer_idx += 1

        # 计算CVD
        return self._calculate_cvd_batch(len(ticks))

    def _calculate_cvd_batch(self, n_new_ticks: int) -> Dict[int, np.ndarray]:
        """
        批量计算CVD（Numpy加速）

        Args:
            n_new_ticks: 新添加的Tick数

        Returns:
            CVD结果字典
        """
        if self.buffer_idx < 1:
            return {}

        results = {}

        for window in self.window_sizes:
            if window > self.buffer_idx:
                continue

            # 使用Numpy滑动窗口计算CVD
            cvd_values = self._numpy_sliding_cvd(window)
            results[window] = cvd_values

        return results

    def _numpy_sliding_cvd(self, window: int) -> np.ndarray:
        """
        Numpy实现的滑动窗口CVD计算

        Args:
            window: 窗口大小

        Returns:
            CVD数组
        """
        # 创建买卖成交量数组
        buy_mask = (self.side_buffer[:self.buffer_idx] == 1)
        sell_mask = (self.side_buffer[:self.buffer_idx] == -1)

        buy_volumes = self.size_buffer[:self.buffer_idx] * buy_mask
        sell_volumes = self.size_buffer[:self.buffer_idx] * sell_mask

        # 计算滑动窗口总和
        if window <= 1:
            return buy_volumes - sell_volumes

        # 使用卷积计算滑动窗口总和
        from scipy.signal import convolve

        # 创建单位窗口
        window_kernel = np.ones(window)

        # 计算滑动窗口买入成交量总和
        buy_sums = convolve(buy_volumes, window_kernel, mode='valid')
        sell_sums = convolve(sell_volumes, window_kernel, mode='valid')

        # 计算CVD
        cvd = buy_sums - sell_sums

        return cvd

    def _resize_buffer(self):
        """调整缓冲区大小"""
        new_size = self.buffer_size * 2

        # 创建新数组
        new_price_buffer = np.zeros(new_size, dtype=np.float64)
        new_size_buffer = np.zeros(new_size, dtype=np.float64)
        new_side_buffer = np.zeros(new_size, dtype=np.int8)

        # 复制数据
        new_price_buffer[:self.buffer_idx] = self.price_buffer[:self.buffer_idx]
        new_size_buffer[:self.buffer_idx] = self.size_buffer[:self.buffer_idx]
        new_side_buffer[:self.buffer_idx] = self.side_buffer[:self.buffer_idx]

        # 更新引用
        self.price_buffer = new_price_buffer
        self.size_buffer = new_size_buffer
        self.side_buffer = new_side_buffer
        self.buffer_size = new_size

        logger.debug(f"CVD缓冲区调整大小: {self.buffer_size}")

    def reset(self):
        """重置计算器状态"""
        self.buffer_idx = 0
        logger.info("BatchCVDCalculator已重置")


# Numba加速函数
@njit(cache=True)
def calculate_cvd_numba(
    sizes: np.ndarray,
    sides: np.ndarray,
    window: int
) -> np.ndarray:
    """
    Numba加速的CVD计算

    Args:
        sizes: 成交量数组
        sides: 方向数组（1=买入, -1=卖出）
        window: 窗口大小

    Returns:
        CVD数组
    """
    n = len(sizes)
    if n < window:
        # 返回部分CVD
        result = np.zeros(n)
        for i in range(n):
            cvd = 0.0
            for j in range(i + 1):
                if sides[j] == 1:
                    cvd += sizes[j]
                elif sides[j] == -1:
                    cvd -= sizes[j]
            result[i] = cvd
        return result

    # 计算完整窗口CVD
    result = np.zeros(n - window + 1)

    # 计算第一个窗口
    cvd = 0.0
    for j in range(window):
        if sides[j] == 1:
            cvd += sizes[j]
        elif sides[j] == -1:
            cvd -= sizes[j]
    result[0] = cvd

    # 滑动计算后续窗口
    for i in range(1, n - window + 1):
        # 减去离开窗口的Tick
        if sides[i - 1] == 1:
            cvd -= sizes[i - 1]
        elif sides[i - 1] == -1:
            cvd += sizes[i - 1]

        # 加上进入窗口的Tick
        if sides[i + window - 1] == 1:
            cvd += sizes[i + window - 1]
        elif sides[i + window - 1] == -1:
            cvd -= sizes[i + window - 1]

        result[i] = cvd

    return result


if __name__ == "__main__":
    # 简单测试
    logger = get_logger(__name__)

    # 创建测试数据
    n_ticks = 1000
    test_ticks = []
    for i in range(n_ticks):
        price = 3000.0 + np.random.randn() * 10
        size = np.random.uniform(0.1, 5.0)
        side = 1 if np.random.rand() > 0.5 else -1

        tick = NormalizedTick(
            ts=i * 1_000_000,
            px=price,
            sz=size,
            side=side
        )
        test_ticks.append(tick)

    # 测试CVDCalculator
    calculator = CVDCalculator(window_sizes=[10, 30, 60])

    import time
    start_time = time.perf_counter()

    for tick in test_ticks[:100]:
        cvd_values = calculator.on_tick(tick)

    elapsed = time.perf_counter() - start_time
    logger.info(f"CVDCalculator处理100个Tick耗时: {elapsed*1000:.1f}ms")
    logger.info(f"当前CVD值: {calculator.get_current_cvd()}")
    logger.info(f"CVD统计: {calculator.get_statistics()}")

    # 测试BatchCVDCalculator
    batch_calculator = BatchCVDCalculator(window_sizes=[10, 30, 60])

    start_time = time.perf_counter()
    batch_results = batch_calculator.add_ticks(test_ticks[:500])
    elapsed = time.perf_counter() - start_time

    logger.info(f"BatchCVDCalculator处理500个Tick耗时: {elapsed*1000:.1f}ms")
    for window, cvd_array in batch_results.items():
        logger.info(f"窗口 {window}: CVD数组长度 {len(cvd_array)}, 均值 {np.mean(cvd_array):.2f}")