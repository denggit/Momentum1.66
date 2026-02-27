#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd


class ReversalStrategy:
    def __init__(self, rsi_oversold=30, rsi_overbought=70):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['Signal'] = 0

        # 1. 基础极值条件：今天或昨天刺穿过 2.5 倍极度通道
        lower_pierced = df['low'] < df['BB_lower_rev']
        upper_pierced = df['high'] > df['BB_upper_rev']

        rsi_oversold = df['RSI'] < self.rsi_oversold
        rsi_overbought = df['RSI'] > self.rsi_overbought

        # 2. 【核心】右侧确认：绝不盲接飞刀！
        # 收阳线（做多）或 收阴线（做空），并且收盘价必须安全回到布林带内部
        is_green = df['close'] > df['open']
        is_red = df['close'] < df['open']

        closed_inside_lower = df['close'] > df['BB_lower_rev']
        closed_inside_upper = df['close'] < df['BB_upper_rev']

        # 做多：(前两根K线跌破下轨) + (RSI超卖) + (今天收阳) + (回到通道内)
        long_cond = (
                (lower_pierced | lower_pierced.shift(1)) &
                (rsi_oversold | rsi_oversold.shift(1)) &
                is_green & closed_inside_lower
        )

        # 做空：反之
        short_cond = (
                (upper_pierced | upper_pierced.shift(1)) &
                (rsi_overbought | rsi_overbought.shift(1)) &
                is_red & closed_inside_upper
        )

        df.loc[long_cond, 'Signal'] = 1
        df.loc[short_cond, 'Signal'] = -1

        # 3. 【强力冷却器】如果前 3 根 K 线内开过仓，强制静默！防止连环爆仓！
        for i in range(1, 4):
            df.loc[df['Signal'].shift(i) != 0, 'Signal'] = 0

        return df