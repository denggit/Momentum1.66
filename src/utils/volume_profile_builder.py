#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/12/26 8:57 PM
@File       : volume_profile_builder.py
@Description: 
"""
import numpy as np
import pandas as pd
from scipy.signal import find_peaks

import numpy as np
import pandas as pd
from scipy.signal import find_peaks


class FabioProfileBuilder:
    def __init__(self, value_area_pct=0.70, bin_size=0.5):
        """
        value_area_pct: 价值区间包含的成交量比例
        bin_size: 每个价格格子的高度 (绝对价格单位，如 0.5 USDT)
        """
        self.value_area_pct = value_area_pct
        self.bin_size = bin_size

    def build_profile(self, df_1m: pd.DataFrame):
        if df_1m.empty:
            return None

        # 1. 获取高低点
        min_price = df_1m['low'].min()
        max_price = df_1m['high'].max()

        # 核心改动：动态计算需要多少个格子
        # np.ceil 向上取整，确保包容最高价
        num_bins = int(np.ceil((max_price - min_price) / self.bin_size))
        num_bins = max(1, num_bins)  # 保底防御

        # 按固定的 bin_size 生成价格边界数组
        price_bins = np.array([min_price + i * self.bin_size for i in range(num_bins + 1)])

        # 初始化成交量数组
        volume_profile = np.zeros(num_bins)

        # 2. 均匀分配成交量
        for _, row in df_1m.iterrows():
            # 找到这根 1m K 线落入的格子区间
            start_idx = int((row['low'] - min_price) / self.bin_size)
            end_idx = int((row['high'] - min_price) / self.bin_size)

            # 防御性边界限制
            start_idx = max(0, min(num_bins - 1, start_idx))
            end_idx = max(0, min(num_bins - 1, end_idx))

            num_bins_crossed = end_idx - start_idx + 1
            vol_per_bin = row['volume'] / num_bins_crossed

            # 切片赋值，把成交量加进去
            volume_profile[start_idx:end_idx + 1] += vol_per_bin

        # 3. 寻找主 POC
        poc_idx = np.argmax(volume_profile)
        poc_price = price_bins[poc_idx] + (self.bin_size / 2)  # 取格子中心价

        # 4. 计算 VAH 和 VAL (双向扩展逻辑与之前完全一样)
        total_volume = np.sum(volume_profile)
        target_volume = total_volume * self.value_area_pct

        current_volume = volume_profile[poc_idx]
        upper_idx = poc_idx
        lower_idx = poc_idx

        while current_volume < target_volume:
            upper_vol = volume_profile[upper_idx + 1] if upper_idx < len(volume_profile) - 1 else 0
            lower_vol = volume_profile[lower_idx - 1] if lower_idx > 0 else 0

            if upper_vol == 0 and lower_vol == 0:
                break

            if upper_vol >= lower_vol:
                upper_idx += 1
                current_volume += volume_profile[upper_idx]
            else:
                lower_idx -= 1
                current_volume += volume_profile[lower_idx]

        # VAH 是上方格子的上限，VAL 是下方格子的下限
        vah_price = price_bins[upper_idx + 1]
        val_price = price_bins[lower_idx]

        # 5. 寻找次级 HVN (使用 scipy.signal.find_peaks)
        peaks, _ = find_peaks(volume_profile, prominence=np.max(volume_profile) * 0.1)
        hvns = []
        for p in peaks:
            if p != poc_idx:
                hvns.append({
                    'price': price_bins[p] + (self.bin_size / 2),
                    'volume': volume_profile[p]
                })

        hvns = sorted(hvns, key=lambda x: x['volume'], reverse=True)

        return {
            'poc': poc_price,
            'vah': vah_price,
            'val': val_price,
            'hvns': [h['price'] for h in hvns],
            'bin_size_used': self.bin_size  # 记录当前使用的精度
        }