#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/26/26 9:08 PM
@File       : squeeze.py
@Description: 
"""
import logging

import pandas as pd


class SqueezeStrategy:
    def __init__(self, volume_factor: float = 1.5):
        """
        初始化挤压突破策略
        :param volume_factor: 突破时，成交量必须是平时均量的多少倍
        """
        self.volume_factor = volume_factor

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        根据计算好的指标，生成交易信号
        """
        # 1. 侦测挤压状态 (Squeeze On)
        # 核心逻辑：布林带的上下轨，彻底缩进了凯特纳通道的上下轨之内
        df['Squeeze_On'] = (df['BB_lower'] > df['KC_lower']) & (df['BB_upper'] < df['KC_upper'])

        # 2. 侦测释放状态 (Squeeze Off)
        df['Squeeze_Off'] = ~df['Squeeze_On']

        # 3. 寻找爆发点 (Breakout)
        # 条件：上一根 K 线还在挤压，这一根突然解除挤压，且价格突破轨线，伴随成倍放量！

        # -- 多头突破 (LONG) --
        long_cond = (
                (df['Squeeze_On'].shift(1) == True) &  # 前一秒还在蓄力
                (df['Squeeze_Off'] == True) &  # 这一刻爆发
                (df['close'] > df['BB_upper']) &  # 价格暴力击穿上轨
                (df['volume'] > df['Vol_SMA'] * self.volume_factor)  &
                (df['close'] > df['EMA_200'])
        )

        # -- 空头突破 (SHORT) --
        short_cond = (
                (df['Squeeze_On'].shift(1) == True) &
                (df['Squeeze_Off'] == True) &
                (df['close'] < df['BB_lower']) &  # 价格暴力砸穿下轨
                (df['volume'] > df['Vol_SMA'] * self.volume_factor) &
                (df['close'] < df['EMA_200'])
        )

        # 初始化信号列为 0 (无操作)
        df['Signal'] = 0

        # 满足条件则打上信号标签
        df.loc[long_cond, 'Signal'] = 1  # 1 代表做多
        df.loc[short_cond, 'Signal'] = -1  # -1 代表做空

        # 统计一下这段数据里产生了多少次信号
        long_count = len(df[df['Signal'] == 1])
        short_count = len(df[df['Signal'] == -1])
        logging.info(f"信号扫描完毕: 发现 {long_count} 次做多机会，{short_count} 次做空机会。")

        return df
