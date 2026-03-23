#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Range Bar工具函数 - 公共方法库
提供两种创建Range Bar的方法：
1. 从OHLC K线数据创建（用于回测）
2. 从Tick数据创建（用于实盘）

设计目标：
- 保持接口简单易用
- 支持回测和实盘的统一地图
- 与四号引擎的数据结构兼容
"""

from typing import List, Optional, Union
import pandas as pd
import numpy as np

from src.strategy.triplea.core.data_structures import (
    NormalizedTick, RangeBar, RangeBarConfig
)


def create_range_bars_from_ohlc(
    df: pd.DataFrame,
    tick_range: int = 150,
    tick_size: float = 0.01,
    max_bars: Optional[int] = None
) -> pd.DataFrame:
    """
    从OHLC K线数据创建Range Bar（回测专用）

    由于K线数据已经是聚合数据，我们无法获得精确的Tick级别成交量和时间戳，
    因此该方法主要关注价格信息，成交量等信息可能不精确。

    Args:
        df: OHLC DataFrame，必须包含以下列：
            - 'open' / 'high' / 'low' / 'close' / 'volume'（可选）
            - 索引应为时间戳（datetime类型）
        tick_range: Range Bar的价格范围（Tick单位），默认150个Tick（1.5U）
        tick_size: 最小价格变动单位，默认0.01（ETH永续合约）
        max_bars: 最大生成的Bar数量，None表示无限制

    Returns:
        pd.DataFrame: Range Bar数据，包含以下列：
            - 'open_ts': 开盘时间戳（从原始数据推断）
            - 'open_px': 开盘价
            - 'high_px': 最高价
            - 'low_px': 最低价
            - 'close_px': 收盘价
            - 'total_buy_vol': 买入成交量（如果有volume列）
            - 'total_sell_vol': 卖出成交量（如果有volume列）
            - 'delta': 净成交量（买入-卖出）
            - 'tick_count': 包含的原始K线数量

    Raises:
        ValueError: 如果输入DataFrame不包含必需的列
    """
    # 验证输入列
    required_cols = ['open', 'high', 'low', 'close']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"DataFrame缺少必需的列: {missing_cols}")

    # 准备结果列表
    range_bars = []
    current_bar = None
    open_price_base = 0.0

    # 是否有成交量信息
    has_volume = 'volume' in df.columns

    # 按顺序处理每一行
    for idx, row in df.iterrows():
        # 获取价格
        open_px = row['open']
        high_px = row['high']
        low_px = row['low']
        close_px = row['close']

        # 尝试获取时间戳（使用索引或列）
        if hasattr(idx, 'timestamp'):
            # 如果是datetime索引
            ts_ns = int(idx.timestamp() * 1e9)
        elif 'timestamp' in df.columns:
            # 如果有timestamp列
            ts_ns = int(row['timestamp'] * 1e9)
        else:
            # 使用序号
            ts_ns = len(range_bars) * 1000000000

        # 获取成交量（如果有）
        volume = row['volume'] if has_volume else 0.0
        # 简单假设50%为买入，50%为卖出（实际中可能需要更复杂的逻辑）
        buy_vol = volume * 0.5
        sell_vol = volume * 0.5

        if current_bar is None:
            # 初始化第一个Bar
            current_bar = {
                'open_ts': ts_ns,
                'open_px': open_px,
                'high_px': high_px,
                'low_px': low_px,
                'close_px': close_px,
                'total_buy_vol': buy_vol,
                'total_sell_vol': sell_vol,
                'delta': buy_vol - sell_vol,
                'tick_count': 1
            }
            open_price_base = open_px
            continue

        # 更新当前Bar的最高最低价
        current_bar['high_px'] = max(current_bar['high_px'], high_px)
        current_bar['low_px'] = min(current_bar['low_px'], low_px)
        current_bar['close_px'] = close_px

        # 更新成交量信息
        current_bar['total_buy_vol'] += buy_vol
        current_bar['total_sell_vol'] += sell_vol
        current_bar['delta'] = current_bar['total_buy_vol'] - current_bar['total_sell_vol']
        current_bar['tick_count'] += 1

        # 检查是否达到闭合条件
        # 计算从开盘基准价到当前收盘价的位移（Tick单位）
        displacement = abs(close_px - open_price_base) / tick_size

        if displacement >= tick_range:
            # Bar闭合，添加到结果
            range_bars.append(current_bar.copy())

            # 新开Bar，使用当前收盘价作为新Bar的开盘价
            current_bar = {
                'open_ts': ts_ns,
                'open_px': close_px,  # 使用当前收盘价作为新开盘价
                'high_px': close_px,
                'low_px': close_px,
                'close_px': close_px,
                'total_buy_vol': buy_vol,
                'total_sell_vol': sell_vol,
                'delta': buy_vol - sell_vol,
                'tick_count': 1
            }
            open_price_base = close_px  # 重置基准价

            # 检查是否达到最大Bar数量限制
            if max_bars is not None and len(range_bars) >= max_bars:
                break

    # 如果还有未闭合的Bar，添加到结果
    if current_bar is not None:
        range_bars.append(current_bar)

    # 转换为DataFrame
    result_df = pd.DataFrame(range_bars)

    # 确保数据类型正确
    if not result_df.empty:
        result_df['open_ts'] = result_df['open_ts'].astype(np.int64)

    return result_df


def create_range_bars_from_ticks(
    ticks: List[NormalizedTick],
    tick_range: int = 150,
    tick_size: float = 0.01,
    max_bars: Optional[int] = None
) -> List[RangeBar]:
    """
    从Tick数据创建Range Bar（实盘专用）

    使用精确的Tick级别数据生成Range Bar，包含准确的：
    - 时间戳（纳秒精度）
    - 成交量（买入/卖出分解）
    - 价格范围

    Args:
        ticks: NormalizedTick对象列表
        tick_range: Range Bar的价格范围（Tick单位），默认150个Tick（1.5U）
        tick_size: 最小价格变动单位，默认0.01（ETH永续合约）
        max_bars: 最大生成的Bar数量，None表示无限制

    Returns:
        List[RangeBar]: RangeBar对象列表

    Raises:
        ValueError: 如果ticks列表为空
    """
    if not ticks:
        raise ValueError("ticks列表不能为空")

    range_bars = []
    current_bar = None
    open_price_base = 0.0

    for tick in ticks:
        if current_bar is None:
            # 初始化第一个Bar
            current_bar = RangeBar(
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
            open_price_base = tick.px
            continue

        # 更新当前Bar的最高最低价
        current_bar.high_px = max(current_bar.high_px, tick.px)
        current_bar.low_px = min(current_bar.low_px, tick.px)
        current_bar.close_px = tick.px

        # 更新成交量信息
        if tick.side == 1:
            current_bar.total_buy_vol += tick.sz
        else:
            current_bar.total_sell_vol += tick.sz

        current_bar.delta = current_bar.total_buy_vol - current_bar.total_sell_vol
        current_bar.tick_count += 1

        # 检查是否达到闭合条件
        displacement = abs(tick.px - open_price_base) / tick_size

        if displacement >= tick_range:
            # Bar闭合，添加到结果
            range_bars.append(current_bar)

            # 新开Bar，使用当前Tick作为新Bar的开始
            current_bar = RangeBar(
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
            open_price_base = tick.px  # 重置基准价

            # 检查是否达到最大Bar数量限制
            if max_bars is not None and len(range_bars) >= max_bars:
                break

    # 如果还有未闭合的Bar，添加到结果
    if current_bar is not None and (max_bars is None or len(range_bars) < max_bars):
        range_bars.append(current_bar)

    return range_bars


def create_range_bars(
    data: Union[pd.DataFrame, List[NormalizedTick]],
    tick_range: int = 150,
    tick_size: float = 0.01,
    max_bars: Optional[int] = None
) -> Union[pd.DataFrame, List[RangeBar]]:
    """
    通用函数：根据输入数据类型自动选择创建Range Bar的方法

    Args:
        data: 输入数据，可以是：
            - pd.DataFrame（OHLC数据）
            - List[NormalizedTick]（Tick数据）
        tick_range: Range Bar的价格范围（Tick单位）
        tick_size: 最小价格变动单位
        max_bars: 最大生成的Bar数量

    Returns:
        根据输入类型返回：
            - pd.DataFrame（如果输入是DataFrame）
            - List[RangeBar]（如果输入是Tick列表）

    Raises:
        TypeError: 如果输入数据类型不支持
    """
    if isinstance(data, pd.DataFrame):
        return create_range_bars_from_ohlc(
            data, tick_range, tick_size, max_bars
        )
    elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], NormalizedTick):
        return create_range_bars_from_ticks(
            data, tick_range, tick_size, max_bars
        )
    else:
        raise TypeError(f"不支持的数据类型: {type(data)}")


def range_bars_to_dataframe(range_bars: List[RangeBar]) -> pd.DataFrame:
    """
    将RangeBar对象列表转换为DataFrame

    Args:
        range_bars: RangeBar对象列表

    Returns:
        pd.DataFrame: 包含所有RangeBar数据的DataFrame
    """
    if not range_bars:
        return pd.DataFrame()

    data = []
    for bar in range_bars:
        data.append({
            'open_ts': bar.open_ts,
            'open_px': bar.open_px,
            'high_px': bar.high_px,
            'low_px': bar.low_px,
            'close_px': bar.close_px,
            'total_buy_vol': bar.total_buy_vol,
            'total_sell_vol': bar.total_sell_vol,
            'delta': bar.delta,
            'tick_count': bar.tick_count
        })

    return pd.DataFrame(data)