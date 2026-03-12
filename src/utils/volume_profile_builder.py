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


class FabioProfileBuilder:
    def __init__(self, value_area_pct=0.70, bins=200):
        """
        value_area_pct: 价值区间包含的成交量比例 (通常 68% 或 70%)
        bins: 价格分箱的数量 (越多越精细，但太细会有噪音)
        """
        self.value_area_pct = value_area_pct
        self.bins = bins

    def build_profile(self, df_1m: pd.DataFrame):
        """
        输入: 过去 24 小时的 1分钟 K线 DataFrame (包含 high, low, close, volume)
        输出: dict 包含 vah, val, poc, hvns 等
        """
        if df_1m.empty:
            return None

        # 1. 确定价格范围并创建价格分箱 (Bins)
        min_price = df_1m['low'].min()
        max_price = df_1m['high'].max()
        price_bins = np.linspace(min_price, max_price, self.bins)

        # 初始化每个格子的成交量为0
        volume_profile = np.zeros(self.bins - 1)

        # 2. 将 1m K线的成交量分配到对应的价格格子中
        # 简单高效的分配法：将单根 K 线的成交量均匀分摊到其 High 和 Low 之间的所有格子里
        for _, row in df_1m.iterrows():
            # 找到这根 K 线穿过了哪些格子
            start_idx = np.searchsorted(price_bins, row['low']) - 1
            end_idx = np.searchsorted(price_bins, row['high'])

            start_idx = max(0, start_idx)
            end_idx = min(self.bins - 1, end_idx)

            num_bins_crossed = end_idx - start_idx
            if num_bins_crossed > 0:
                # 均匀分配成交量
                vol_per_bin = row['volume'] / num_bins_crossed
                volume_profile[start_idx:end_idx] += vol_per_bin

        # 3. 寻找主 POC (Point of Control)
        poc_idx = np.argmax(volume_profile)
        poc_price = (price_bins[poc_idx] + price_bins[poc_idx + 1]) / 2

        # 4. 计算 VAH 和 VAL (价值区间计算)
        total_volume = np.sum(volume_profile)
        target_volume = total_volume * self.value_area_pct

        current_volume = volume_profile[poc_idx]
        upper_idx = poc_idx
        lower_idx = poc_idx

        # 双向扩展算法：每次比较上下哪个格子的成交量更大，就吞并哪个格子
        while current_volume < target_volume:
            upper_vol = volume_profile[upper_idx + 1] if upper_idx < len(volume_profile) - 1 else 0
            lower_vol = volume_profile[lower_idx - 1] if lower_idx > 0 else 0

            if upper_vol == 0 and lower_vol == 0:
                break  # 数据耗尽

            if upper_vol >= lower_vol:
                upper_idx += 1
                current_volume += volume_profile[upper_idx]
            else:
                lower_idx -= 1
                current_volume += volume_profile[lower_idx]

        vah_price = price_bins[upper_idx + 1]
        val_price = price_bins[lower_idx]

        # 5. 寻找次级 HVN (宏观支撑/阻力目标位)
        # prominence 控制峰的突出程度，过滤掉小噪音
        peaks, _ = find_peaks(volume_profile, prominence=np.max(volume_profile) * 0.1)
        hvns = []
        for p in peaks:
            if p != poc_idx:  # 排除主 POC
                hvns.append({
                    'price': (price_bins[p] + price_bins[p + 1]) / 2,
                    'volume': volume_profile[p]
                })

        # 按成交量从大到小排序
        hvns = sorted(hvns, key=lambda x: x['volume'], reverse=True)

        return {
            'poc': poc_price,
            'vah': vah_price,
            'val': val_price,
            'hvns': [h['price'] for h in hvns]  # 返回价格列表供引擎调用
        }