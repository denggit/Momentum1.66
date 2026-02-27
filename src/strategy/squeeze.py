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
        df['Squeeze_On'] = (df['BB_lower'] > df['KC_lower']) & (df['BB_upper'] < df['KC_upper'])
        df['Squeeze_Off'] = ~df['Squeeze_On']

        # 【新增手术】：计算弹簧连续被压缩的次数！
        # 这个高端的 pandas 写法能统计当前处于连续第几根 Squeeze_On 状态
        df['Squeeze_Count'] = df['Squeeze_On'].groupby((~df['Squeeze_On']).cumsum()).cumsum()

        # 核心参数：弹簧必须至少被压住 5 根 K 线 (75分钟) 才能释放！
        min_squeeze_duration = 5

        # -- 多头突破 (LONG) --
        long_cond = (
                (df['Squeeze_On'].shift(1)) &  
                (df['Squeeze_Count'].shift(1) >= min_squeeze_duration) & # <--- 拒绝早泄，必须深度蓄能！
                (df['Squeeze_Off']) &  
                (df['close'] > df['BB_upper']) &  
                (df['volume'] > df['Vol_SMA'] * self.volume_factor) &
                (df['close'] > df['EMA_200'])  
        )

        # -- 空头突破 (SHORT) --
        short_cond = (
                (df['Squeeze_On'].shift(1)) &
                (df['Squeeze_Count'].shift(1) >= min_squeeze_duration) & # <--- 拒绝早泄！
                (df['Squeeze_Off']) &
                (df['close'] < df['BB_lower']) &  
                (df['volume'] > df['Vol_SMA'] * self.volume_factor) &
                (df['close'] < df['EMA_200'])  
        )

        df['Signal'] = 0
        df.loc[long_cond, 'Signal'] = 1  
        df.loc[short_cond, 'Signal'] = -1  

        return df
