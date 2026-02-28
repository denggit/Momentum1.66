#!/usr/bin/env python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd


class SMCStrategy:
    # 【新增】entry_buffer=0.3，意思是只要价格靠近订单块边缘 0.3 倍 ATR 的距离，立刻抢跑进场！
    def __init__(self, ema_period=144, lookback=15, atr_mult=1.5, ob_expiry=72, sl_buffer=0.6, entry_buffer=-0.1):
        self.ema_period = ema_period
        self.lookback = lookback
        self.atr_mult = atr_mult
        self.ob_expiry = ob_expiry
        self.sl_buffer = sl_buffer
        self.entry_buffer = entry_buffer  # 进场提前量缓冲

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['Signal'] = 0
        df['SL_Price'] = np.nan

        open_p = df['open'].values
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        atr = df['ATR'].values
        ema = df['EMA_144'].values

        highest_high = df['high'].rolling(self.lookback).max().shift(1).values
        lowest_low = df['low'].rolling(self.lookback).min().shift(1).values

        signals = np.zeros(len(df))
        sl_prices = np.full(len(df), np.nan)

        long_ob_top = 0.0
        long_ob_bot = 0.0
        long_ob_active = False
        long_ob_age = 0

        short_ob_top = float('inf')
        short_ob_bot = float('inf')
        short_ob_active = False
        short_ob_age = 0

        for i in range(self.lookback, len(df)):

            # 0. 订单块老化机制
            if long_ob_active:
                long_ob_age += 1
                if long_ob_age > self.ob_expiry:
                    long_ob_active = False

            if short_ob_active:
                short_ob_age += 1
                if short_ob_age > self.ob_expiry:
                    short_ob_active = False

                    # 1. 猎杀时刻 (Mitigation - 加上了提前抢跑逻辑)
            if long_ob_active:
                # 【核心】允许价格在订单块上方 entry_buffer 的位置提前触发！
                long_entry_trigger = long_ob_top + (atr[i] * self.entry_buffer)

                if low[i] <= long_entry_trigger and close[i] > long_ob_bot:
                    signals[i] = 1
                    # 止损依然放在最底下，加缓冲防插针
                    sl_prices[i] = long_ob_bot - (atr[i] * self.sl_buffer)
                    long_ob_active = False
                elif close[i] < long_ob_bot:
                    long_ob_active = False

            if short_ob_active:
                # 【核心】允许价格在空头订单块下方 entry_buffer 的位置提前触发！
                short_entry_trigger = short_ob_bot - (atr[i] * self.entry_buffer)

                if high[i] >= short_entry_trigger and close[i] < short_ob_top:
                    signals[i] = -1
                    sl_prices[i] = short_ob_top + (atr[i] * self.sl_buffer)
                    short_ob_active = False
                elif close[i] > short_ob_top:
                    short_ob_active = False

            # 2. 寻找动能建仓结构
            # 多头结构
            if close[i] > ema[i]:
                if close[i] > open_p[i] and (close[i] - open_p[i]) > self.atr_mult * atr[i]:
                    if close[i] > highest_high[i]:
                        for j in range(i - 1, max(-1, i - 10), -1):
                            if close[j] < open_p[j]:
                                long_ob_top = high[j]
                                long_ob_bot = low[j]
                                long_ob_active = True
                                long_ob_age = 0
                                break

                                # 空头结构
            elif close[i] < ema[i]:
                if close[i] < open_p[i] and (open_p[i] - close[i]) > self.atr_mult * atr[i]:
                    if close[i] < lowest_low[i]:
                        for j in range(i - 1, max(-1, i - 10), -1):
                            if close[j] > open_p[j]:
                                short_ob_top = high[j]
                                short_ob_bot = low[j]
                                short_ob_active = True
                                short_ob_age = 0
                                break

        df['Signal'] = signals
        df['SL_Price'] = sl_prices
        return df