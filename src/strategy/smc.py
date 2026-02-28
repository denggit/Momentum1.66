#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/28/26 9:39 PM
@File       : smc.py
@Description: 
"""
import numpy as np
import pandas as pd


class SMCStrategy:
    def __init__(self, ema_period=144, lookback=15, atr_mult=1.5):
        self.ema_period = ema_period
        self.lookback = lookback  # 看过去多少根 K 线来确认破位 (BOS)
        self.atr_mult = atr_mult  # 突破 K 线的实体必须大于 1.5 倍 ATR 才算真动能

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['Signal'] = 0

        # 将 Pandas 序列转换为 Numpy 数组，大幅提升循环遍历的速度
        open_p = df['open'].values
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        atr = df['ATR'].values
        ema = df['EMA_144'].values

        # 预计算前 N 根 K 线的最高点和最低点 (用于判定结构破位 BOS)
        highest_high = df['high'].rolling(self.lookback).max().shift(1).values
        lowest_low = df['low'].rolling(self.lookback).min().shift(1).values

        signals = np.zeros(len(df))

        # 记录当前活跃的多头/空头订单块 (Order Block)
        long_ob_top = 0.0
        long_ob_bot = 0.0
        long_ob_active = False

        short_ob_top = float('inf')
        short_ob_bot = float('inf')
        short_ob_active = False

        # 遍历每一根 K 线，模拟实盘的“状态记忆”
        for i in range(self.lookback, len(df)):

            # ====================================
            # 1. 猎杀时刻：如果价格回踩了未被破坏的订单块
            # ====================================
            if long_ob_active:
                # 价格刺入订单块 (low <= top)，且收盘没有跌穿订单块 (close > bot)
                if low[i] <= long_ob_top and close[i] > long_ob_bot:
                    signals[i] = 1
                    long_ob_active = False  # 订单块被“缓解(Mitigated)”，消耗完毕
                elif close[i] < long_ob_bot:
                    long_ob_active = False  # 订单块被无情跌穿，逻辑失效(Invalidated)

            if short_ob_active:
                if high[i] >= short_ob_bot and close[i] < short_ob_top:
                    signals[i] = -1
                    short_ob_active = False
                elif close[i] > short_ob_top:
                    short_ob_active = False

            # ====================================
            # 2. 寻找建仓结构：动能破位 (BOS) -> 标记订单块 (OB)
            # ====================================
            # 多头结构：必须在 EMA144 之上
            if close[i] > ema[i]:
                # 必须是大阳线 (动能 K 线)
                if close[i] > open_p[i] and (close[i] - open_p[i]) > self.atr_mult * atr[i]:
                    # 必须突破前期高点 (Bullish BOS)
                    if close[i] > highest_high[i]:
                        # 往前倒推，寻找突破前的“最后一根阴线”，这就是机构的洗盘订单块！
                        for j in range(i - 1, max(-1, i - 10), -1):
                            if close[j] < open_p[j]:  # 找到阴线
                                long_ob_top = high[j]
                                long_ob_bot = low[j]
                                long_ob_active = True
                                break  # 标记完毕，停止寻找

            # 空头结构：必须在 EMA144 之下
            elif close[i] < ema[i]:
                # 必须是大阴线 (动能 K 线)
                if close[i] < open_p[i] and (open_p[i] - close[i]) > self.atr_mult * atr[i]:
                    # 必须跌穿前期低点 (Bearish BOS)
                    if close[i] < lowest_low[i]:
                        # 往前倒推，寻找突破前的“最后一根阳线”，这就是空头的砸盘订单块！
                        for j in range(i - 1, max(-1, i - 10), -1):
                            if close[j] > open_p[j]:  # 找到阳线
                                short_ob_top = high[j]
                                short_ob_bot = low[j]
                                short_ob_active = True
                                break

        df['Signal'] = signals
        return df