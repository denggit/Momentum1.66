#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd

class ReversalStrategy:
    def __init__(self, vol_multiplier=2.0):
        # 要求突破时的成交量必须是平均成交量的 2 倍以上！
        self.vol_multiplier = vol_multiplier

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['Signal'] = 0
        
        # 1. 之前必须在深水区潜伏：上一根 K 线还在 EMA 200 之下
        was_below = df['close'].shift(1) < df['EMA_200'].shift(1)
        
        # 2. 强力突破：当前 K 线收盘价站上 EMA 200
        cross_up = df['close'] > df['EMA_200']
        
        # 3. 巨量确认：绝对不能是缩量假突破！成交量必须大于均量的 N 倍
        vol_surge = df['vol'] > (df['VOL_SMA'] * self.vol_multiplier)
        
        # 4. 实体大阳线：收盘价必须高于开盘价
        is_green = df['close'] > df['open']

        # 终极右侧共振：(之前在水下) + (突破 EMA 200) + (爆出巨量) + (收阳线)
        long_cond = was_below & cross_up & vol_surge & is_green
        
        df.loc[long_cond, 'Signal'] = 1
        
        # 冷却器：开仓后强制休息 5 根 K 线
        for i in range(1, 6):
            df.loc[df['Signal'].shift(i) != 0, 'Signal'] = 0
            
        return df
