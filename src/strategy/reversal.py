#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd


class ReversalStrategy:
    def __init__(self):
        # MACD 背离不需要复杂的参数，主要靠数学逻辑
        pass

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['Signal'] = 0

        # 条件A：大环境必须是“水下”（MACD < 0）且处于明显跌势（收盘价 < EMA20）
        under_water = df['MACD'] < 0
        downtrend = df['close'] < df['EMA_20']

        # 条件B：刚刚发生“水下金叉”（MACD线 向上击穿 Signal线）
        golden_cross = (df['MACD'] > df['MACD_signal']) & (df['MACD'].shift(1) <= df['MACD_signal'].shift(1))

        # 条件C：【核心背离逻辑】(Divergence)
        # 当前价格比 15 根 K 线前更低 (价格创新低)
        # 但当前的 MACD 值却比 15 根 K 线前更高！(动能没创新低，说明空头力竭)
        price_lower = df['close'] < df['close'].shift(15)
        momentum_higher = df['MACD'] > df['MACD'].shift(15)
        divergence = price_lower & momentum_higher

        # 条件D：右侧确认，必须是收阳线，多头实打实地掏出了真金白银
        is_green = df['close'] > df['open']

        # 终极共振：(处于跌势) + (水下金叉) + (动能底背离) + (收阳线确认)
        long_cond = downtrend & golden_cross & under_water & divergence & is_green

        df.loc[long_cond, 'Signal'] = 1

        # 冷却器：开仓后强制休息 5 根 K 线，避开假突破的余震
        for i in range(1, 6):
            df.loc[df['Signal'].shift(i) != 0, 'Signal'] = 0

        return df