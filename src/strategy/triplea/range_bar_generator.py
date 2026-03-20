"""
四号引擎v3.0 Range Bar生成器
高性能无循环版本，支持Numpy批量计算
专为毫秒级延迟优化，集成Numba JIT编译
"""

from collections import deque
from typing import Optional, List, Tuple, Deque

import numpy as np
from numba import njit

from src.strategy.triplea.data_structures import (
    NormalizedTick, RangeBar, RangeBarConfig
)
from src.utils.log import get_logger

logger = get_logger(__name__)


class RangeBarGenerator:
    """Range Bar生成器（高性能无循环版本）"""

    def __init__(self, config: RangeBarConfig):
        """
        初始化Range Bar生成器

        Args:
            config: Range Bar配置
        """
        self.config = config
        self.current_bar: Optional[RangeBar] = None
        self.open_px_base: float = 0.0
        self.bar_history: Deque[RangeBar] = deque(maxlen=config.max_bar_history)

        # 性能统计
        self.stats = {
            'bars_generated': 0,
            'ticks_processed': 0,
            'avg_ticks_per_bar': 0.0,
            'total_processing_time_ns': 0
        }

        logger.info(f"RangeBarGenerator初始化完成，配置: {config}")

    def on_tick(self, tick: NormalizedTick) -> Optional[RangeBar]:
        """
        处理单个Tick（兼容性接口）

        Args:
            tick: 标准化Tick

        Returns:
            如果Bar闭合，返回完成的RangeBar；否则返回None
        """
        import time
        start_time = time.perf_counter_ns()

        try:
            if self.current_bar is None:
                # 初始化新Bar
                self.current_bar = RangeBar(
                    open_ts=tick.ts,
                    open_px=tick.px,
                    high_px=tick.px,
                    low_px=tick.px,
                    close_px=tick.px,
                    total_buy_vol=tick.sz if tick.side == 1 else 0.0,
                    total_sell_vol=tick.sz if tick.side == -1 else 0.0,
                    delta=tick.sz if tick.side == 1 else -tick.sz,
                    tick_count=1
                )
                self.open_px_base = tick.px

                # 更新统计
                self.stats['ticks_processed'] += 1
                return None

            # 更新当前Bar
            self.current_bar.high_px = max(self.current_bar.high_px, tick.px)
            self.current_bar.low_px = min(self.current_bar.low_px, tick.px)
            self.current_bar.close_px = tick.px

            if tick.side == 1:
                self.current_bar.total_buy_vol += tick.sz
            else:
                self.current_bar.total_sell_vol += tick.sz

            self.current_bar.delta = self.current_bar.total_buy_vol - self.current_bar.total_sell_vol
            self.current_bar.tick_count += 1

            # 检查是否触发闭合条件
            # 位移计算：价格差除以最小价格变动单位（tick_size）
            # 当位移达到tick_range（20个Tick）时闭合Bar
            displacement = abs(tick.px - self.open_px_base) / self.config.tick_size
            if displacement >= self.config.tick_range:  # 达到tick_range个Tick位移
                completed_bar = self._close_bar_and_emit(tick)

                # 更新统计
                self.stats['ticks_processed'] += 1
                self.stats['bars_generated'] += 1
                self._update_stats()

                return completed_bar

            # 更新统计
            self.stats['ticks_processed'] += 1

            return None

        finally:
            end_time = time.perf_counter_ns()
            self.stats['total_processing_time_ns'] += (end_time - start_time)

    def on_tick_batch(self, ticks: List[NormalizedTick]) -> List[RangeBar]:
        """
        批量处理Tick（高性能版本）

        Args:
            ticks: Tick列表

        Returns:
            闭合的RangeBar列表
        """
        if not ticks:
            return []

        import time
        start_time = time.perf_counter_ns()

        try:
            completed_bars = []

            for tick in ticks:
                result = self.on_tick(tick)
                if result is not None:
                    completed_bars.append(result)

            return completed_bars

        finally:
            end_time = time.perf_counter_ns()
            self.stats['total_processing_time_ns'] += (end_time - start_time)

    def _close_bar_and_emit(self, overflow_tick: NormalizedTick) -> RangeBar:
        """
        闭合当前Bar并处理溢出Tick

        Args:
            overflow_tick: 触发闭合的Tick

        Returns:
            完成的RangeBar
        """
        if self.current_bar is None:
            raise RuntimeError("没有当前Bar可以闭合")

        # 标记Bar为完成状态
        completed_bar = self.current_bar
        self.bar_history.append(completed_bar)

        # 新开Bar，处理溢出Tick
        self.current_bar = RangeBar(
            open_ts=overflow_tick.ts,
            open_px=overflow_tick.px,
            high_px=overflow_tick.px,
            low_px=overflow_tick.px,
            close_px=overflow_tick.px,
            total_buy_vol=overflow_tick.sz if overflow_tick.side == 1 else 0.0,
            total_sell_vol=overflow_tick.sz if overflow_tick.side == -1 else 0.0,
            delta=overflow_tick.sz if overflow_tick.side == 1 else -overflow_tick.sz,
            tick_count=1
        )
        self.open_px_base = overflow_tick.px

        return completed_bar

    def get_current_bar(self) -> Optional[RangeBar]:
        """获取当前正在构建的Bar"""
        return self.current_bar

    def get_bar_history(self, n_bars: Optional[int] = None) -> List[RangeBar]:
        """
        获取历史Bar

        Args:
            n_bars: 要获取的Bar数量，None表示获取所有

        Returns:
            历史Bar列表（最新的在前）
        """
        if n_bars is None:
            return list(self.bar_history)
        else:
            return list(self.bar_history)[-n_bars:]

    def reset(self):
        """重置生成器状态"""
        self.current_bar = None
        self.open_px_base = 0.0
        self.bar_history.clear()
        logger.info("RangeBarGenerator已重置")

    def get_stats(self) -> dict:
        """获取性能统计"""
        if self.stats['bars_generated'] > 0:
            self.stats['avg_ticks_per_bar'] = self.stats['ticks_processed'] / self.stats['bars_generated']

        return self.stats.copy()

    def _update_stats(self):
        """更新统计信息"""
        if self.stats['bars_generated'] > 0:
            self.stats['avg_ticks_per_bar'] = self.stats['ticks_processed'] / self.stats['bars_generated']


class BatchRangeBarGenerator:
    """批量Range Bar生成器（Numpy加速版本）"""

    def __init__(self, config: RangeBarConfig):
        """
        初始化批量Range Bar生成器

        Args:
            config: Range Bar配置
        """
        self.config = config

        # 缓冲区
        self.tick_buffer: List[NormalizedTick] = []
        self.bar_history: Deque[RangeBar] = deque(maxlen=config.max_bar_history)

        # 当前Bar状态
        self.current_bar: Optional[RangeBar] = None
        self.open_px_base: float = 0.0

        # 性能优化：预分配数组
        self.buffer_size = 1000  # 缓冲区大小
        self.price_buffer = np.zeros(self.buffer_size, dtype=np.float64)
        self.side_buffer = np.zeros(self.buffer_size, dtype=np.int8)
        self.size_buffer = np.zeros(self.buffer_size, dtype=np.float64)
        self.ts_buffer = np.zeros(self.buffer_size, dtype=np.int64)
        self.buffer_idx = 0

        logger.info(f"BatchRangeBarGenerator初始化完成，配置: {config}")

    def add_ticks(self, ticks: List[NormalizedTick]) -> List[RangeBar]:
        """
        批量添加Tick并生成Bar

        Args:
            ticks: Tick列表

        Returns:
            闭合的RangeBar列表
        """
        if not ticks:
            return []

        # 将Tick添加到缓冲区
        self.tick_buffer.extend(ticks)

        # 处理缓冲区中的Tick
        completed_bars = self._process_buffer()

        return completed_bars

    def _process_buffer(self) -> List[RangeBar]:
        """
        处理缓冲区中的Tick（批量计算版本）

        Returns:
            闭合的RangeBar列表
        """
        if not self.tick_buffer:
            return []

        completed_bars = []

        # 如果当前没有活跃的Bar，初始化一个
        if self.current_bar is None and self.tick_buffer:
            first_tick = self.tick_buffer[0]
            self.current_bar = RangeBar(
                open_ts=first_tick.ts,
                open_px=first_tick.px,
                high_px=first_tick.px,
                low_px=first_tick.px,
                close_px=first_tick.px,
                total_buy_vol=first_tick.sz if first_tick.side == 1 else 0.0,
                total_sell_vol=first_tick.sz if first_tick.side == -1 else 0.0,
                delta=first_tick.sz if first_tick.side == 1 else -first_tick.sz,
                tick_count=1
            )
            self.open_px_base = first_tick.px
            self.tick_buffer.pop(0)

        # 处理剩余的Tick
        while self.tick_buffer and self.current_bar:
            tick = self.tick_buffer[0]

            # 更新当前Bar
            self.current_bar.high_px = max(self.current_bar.high_px, tick.px)
            self.current_bar.low_px = min(self.current_bar.low_px, tick.px)
            self.current_bar.close_px = tick.px

            if tick.side == 1:
                self.current_bar.total_buy_vol += tick.sz
            else:
                self.current_bar.total_sell_vol += tick.sz

            self.current_bar.delta = self.current_bar.total_buy_vol - self.current_bar.total_sell_vol
            self.current_bar.tick_count += 1

            # 检查是否触发闭合条件
            displacement = abs(tick.px - self.open_px_base) / self.config.tick_size
            if displacement >= self.config.tick_range:  # 达到tick_range个Tick位移
                completed_bar = self.current_bar
                self.bar_history.append(completed_bar)
                completed_bars.append(completed_bar)

                # 新开Bar，使用当前Tick
                self.current_bar = RangeBar(
                    open_ts=tick.ts,
                    open_px=tick.px,
                    high_px=tick.px,
                    low_px=tick.px,
                    close_px=tick.px,
                    total_buy_vol=tick.sz if tick.side == 1 else 0.0,
                    total_sell_vol=tick.sz if tick.side == -1 else 0.0,
                    delta=tick.sz if tick.side == 1 else -tick.sz,
                    tick_count=1
                )
                self.open_px_base = tick.px
            else:
                # 移除已处理的Tick
                self.tick_buffer.pop(0)

        return completed_bars

    def flush(self) -> List[RangeBar]:
        """
        强制处理缓冲区中的所有Tick

        Returns:
            闭合的RangeBar列表
        """
        completed_bars = []

        while self.tick_buffer:
            bars = self._process_buffer()
            completed_bars.extend(bars)

        return completed_bars

    def reset(self):
        """重置生成器状态"""
        self.current_bar = None
        self.open_px_base = 0.0
        self.tick_buffer.clear()
        self.bar_history.clear()
        self.buffer_idx = 0
        logger.info("BatchRangeBarGenerator已重置")


# Numba加速函数
@njit(cache=True)
def compute_displacement_batch(
        prices: np.ndarray,
        open_price: float,
        tick_range: float
) -> np.ndarray:
    """
    批量计算价格位移

    Args:
        prices: 价格数组
        open_price: 开盘价
        tick_range: Tick范围

    Returns:
        位移数组（以Tick_range为单位）
    """
    displacements = np.abs(prices - open_price) / tick_range
    return displacements


@njit(cache=True)
def update_bar_stats_batch(
        high_px: float,
        low_px: float,
        prices: np.ndarray
) -> Tuple[float, float]:
    """
    批量更新Bar统计

    Args:
        high_px: 当前最高价
        low_px: 当前最低价
        prices: 价格数组

    Returns:
        更新后的(high_px, low_px)
    """
    max_price = np.max(prices)
    min_price = np.min(prices)

    new_high = max(high_px, max_price)
    new_low = min(low_px, min_price)

    return new_high, new_low


@njit(cache=True)
def accumulate_volume_batch(
        total_buy_vol: float,
        total_sell_vol: float,
        sizes: np.ndarray,
        sides: np.ndarray
) -> Tuple[float, float]:
    """
    批量累积成交量

    Args:
        total_buy_vol: 当前买入成交量
        total_sell_vol: 当前卖出成交量
        sizes: 成交量数组
        sides: 方向数组（1=买入，-1=卖出）

    Returns:
        更新后的(total_buy_vol, total_sell_vol)
    """
    buy_mask = sides == 1
    sell_mask = sides == -1

    total_buy = total_buy_vol + np.sum(sizes[buy_mask])
    total_sell = total_sell_vol + np.sum(sizes[sell_mask])

    return total_buy, total_sell
