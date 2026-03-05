import pandas as pd
import numpy as np
from src.utils.log import get_logger

logger = get_logger(__name__)


class VolumeProfileManager:
    """筹码分布计算核心"""

    def __init__(self, bin_size=0.5):
        self.bin_size = bin_size

    def calculate_metrics(self, df_range: pd.DataFrame):
        """计算给定区间的 POC, VAH, VAL"""
        if df_range.empty or len(df_range) < 10:
            return None

        # 1. 确定价格箱体 (Bins)
        min_p = df_range['low'].min()
        max_p = df_range['high'].max()
        bins = np.arange(min_p, max_p + self.bin_size, self.bin_size)

        # 2. 统计分布：将每根K线的成交量均匀分摊到它覆盖的 bins 中 (简化版 VP)
        profile = pd.Series(0.0, index=bins)
        for _, row in df_range.iterrows():
            mask = (bins >= row['low']) & (bins <= row['high'])
            affected_count = np.sum(mask)
            if affected_count > 0:
                profile.iloc[mask] += row['volume'] / affected_count

        # 3. 提取关键位
        poc_price = profile.idxmax()

        # 计算价值区域 (VA: 70% 成交量)
        total_vol = profile.sum()
        sorted_indices = np.argsort(profile.values)[::-1]
        cumulative_vol = 0
        va_bins = []
        for idx in sorted_indices:
            cumulative_vol += profile.values[idx]
            va_bins.append(profile.index[idx])
            if cumulative_vol >= total_vol * 0.7:
                break

        return {
            'poc': float(poc_price),
            'vah': float(max(va_bins)),
            'val': float(min(va_bins)),
            'total_vol': total_vol
        }


class AutoBalanceFinder:
    """自动锚定历史平衡区"""

    def __init__(self, tolerance_pct=0.002):
        self.tolerance_pct = tolerance_pct  # 搜索历史时的价格容差

    def find_last_balance_area(self, df: pd.DataFrame, target_price: float):
        """
        以价找时：在历史数据中寻找价格在 target_price 附近纠缠最久的【最近一个】时间簇
        """
        tolerance = target_price * self.tolerance_pct

        # 1. 找到所有价格经过 target_price 的 K 线索引
        mask = (df['low'] <= target_price + tolerance) & (df['high'] >= target_price - tolerance)
        intersect_indices = np.where(mask)[0]

        if len(intersect_indices) < 15:  # 历史上这个价位没怎么待过，直接判定为真空区
            return None

        # 2. 聚类分析：寻找最近的一个密集时间堆叠 (Time Cluster)
        # 我们从最后一个索引往回找，如果两个索引之间断档超过 30 根 K 线，认为是一个新的平衡区
        last_idx = intersect_indices[-1]
        cluster_start_idx = last_idx

        # 向前追溯，寻找连续性
        for i in range(len(intersect_indices) - 2, -1, -1):
            curr_idx = intersect_indices[i]
            prev_idx = intersect_indices[i + 1]

            # 如果中间断开了超过 30 根 K 线，说明这是上一个平衡区了，我们就取最近的这一个
            if prev_idx - curr_idx > 30:
                cluster_start_idx = prev_idx
                break
            cluster_start_idx = curr_idx

        # 如果这个时间堆叠太薄（比如只待了 5 分钟），不可信
        if last_idx - cluster_start_idx < 10:
            return None

        # 3. 返回这个自动框选出来的 DataFrame
        return df.iloc[cluster_start_idx: last_idx + 1]