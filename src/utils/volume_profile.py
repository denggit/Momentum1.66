import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

from src.utils.log import get_logger

logger = get_logger(__name__)


class CompositeVolumeProfile:
    """宏观全局筹码雷达：用于识别 48 小时内的真实山峰 (HVN) 和山谷 (LVN)"""

    def __init__(self, bin_size=0.5):
        self.bin_size = bin_size

    def analyze_macro_profile(self, df: pd.DataFrame):
        if df is None or df.empty:
            return None

        try:
            # 1. 基础分箱计算
            min_p = df['low'].min()
            max_p = df['high'].max()

            # 增加防御：如果价格没有波动，无法计算分布
            if min_p == max_p:
                return None

            bins = np.arange(min_p, max_p + self.bin_size, self.bin_size)

            price_centers = (bins[:-1] + bins[1:]) / 2
            volumes = np.zeros(len(price_centers))

            # 极速将每根 K 线成交量切片摊入价格区间
            for _, row in df.iterrows():
                # 🌟 优雅重构：直接将返回的 2 个元素的 array 解包成两个清晰的变量
                idx_low, idx_high = np.searchsorted(bins, [row['low'], row['high']])
                if idx_high > idx_low:
                    vol_per_bin = row['volume'] / (idx_high - idx_low)
                    volumes[idx_low:idx_high] += vol_per_bin
                elif idx_low < len(volumes):
                    # 🌟 Doji (十字星) 的成交量累加
                    volumes[idx_low] += row['volume']

            # 2. 🌟 高斯平滑 (消灭散户噪音，保留主力沉淀)
            smoothed_volumes = gaussian_filter1d(volumes, sigma=3)

            # 在计算均值前检查数组
            if len(smoothed_volumes) == 0:
                return None

            # 3. 🌟 智能寻峰 (HVN)
            mean_vol = np.mean(smoothed_volumes)

            # 处理 mean_vol 为 0 或 nan 的情况
            if np.isnan(mean_vol) or mean_vol == 0:
                return None

            # 突出度门槛：山峰必须比周围的山谷高出 0.5 倍的平均成交量
            peak_indices, _ = find_peaks(smoothed_volumes, prominence=mean_vol * 0.5)
            hvns = price_centers[peak_indices]

            # 4. 🌟 智能寻谷 (LVN)
            inverted_volumes = smoothed_volumes * -1
            # 找山谷的要求可以稍微降低一点
            valley_indices, _ = find_peaks(inverted_volumes, prominence=mean_vol * 0.3)
            lvns = price_centers[valley_indices]

            poc_price = price_centers[np.argmax(smoothed_volumes)] if len(smoothed_volumes) > 0 else 0.0

            return {
                'poc': float(poc_price),
                'hvns': hvns.tolist(),
                'lvns': lvns.tolist()
            }
        except Exception as e:
            logger.error(f"❌ [筹码雷达] 计算宏观 Profile 失败: {e}")
            return None

    def calculate_standard_value_area(self, df: pd.DataFrame, value_area_ratio: float = 0.7):
        """
        计算标准价值区间 (Value Area) - Fabio方法
        返回VAH (Value Area High), VAL (Value Area Low), POC (Point of Control)

        参数:
            df: 包含'high', 'low', 'volume'列的DataFrame
            value_area_ratio: 价值区间成交量占比，默认70%

        返回:
            dict: 包含'vah', 'val', 'poc', 'total_volume', 'value_area_volume'
        """
        if df is None or df.empty:
            return None

        try:
            # 1. 基础分箱计算
            min_p = df['low'].min()
            max_p = df['high'].max()

            if min_p == max_p:
                return None

            # 动态分箱：基于ATR或价格波动率
            price_range = max_p - min_p
            if price_range > 0:
                # 使用更精细的分箱（当前分箱大小的1/2）
                dynamic_bin_size = self.bin_size * 0.5
                bins = np.arange(min_p, max_p + dynamic_bin_size, dynamic_bin_size)
            else:
                bins = np.arange(min_p, max_p + self.bin_size, self.bin_size)

            price_centers = (bins[:-1] + bins[1:]) / 2
            volumes = np.zeros(len(price_centers))

            # 2. 成交量分配（带时间加权）
            total_k_lines = len(df)
            for idx, (_, row) in enumerate(df.iterrows()):
                idx_low, idx_high = np.searchsorted(bins, [row['low'], row['high']])

                # 时间加权：最近的数据权重更高
                time_weight = 0.5 + 0.5 * (idx / total_k_lines)  # 0.5到1.0线性增长

                if idx_high > idx_low:
                    vol_per_bin = row['volume'] * time_weight / (idx_high - idx_low)
                    volumes[idx_low:idx_high] += vol_per_bin
                elif idx_low < len(volumes):
                    volumes[idx_low] += row['volume'] * time_weight

            # 3. 找到POC（成交量最高点）
            if len(volumes) == 0:
                return None

            poc_idx = np.argmax(volumes)
            poc = float(price_centers[poc_idx])
            total_volume = float(np.sum(volumes))

            # 4. 计算价值区间（从POC向两侧扩展，直到达到目标成交量占比）
            target_volume = total_volume * value_area_ratio
            sorted_indices = np.argsort(-volumes)  # 按成交量降序排列

            accumulated_volume = 0.0
            value_area_indices = set()

            for idx in sorted_indices:
                accumulated_volume += volumes[idx]
                value_area_indices.add(idx)
                if accumulated_volume >= target_volume:
                    break

            # 5. 找到价值区间边界
            if not value_area_indices:
                return None

            value_area_indices_list = list(value_area_indices)
            vah_idx = max(value_area_indices_list)
            val_idx = min(value_area_indices_list)

            vah = float(price_centers[vah_idx])
            val = float(price_centers[val_idx])

            return {
                'vah': vah,
                'val': val,
                'poc': poc,
                'total_volume': total_volume,
                'value_area_volume': float(accumulated_volume),
                'value_area_ratio': float(accumulated_volume / total_volume) if total_volume > 0 else 0.0,
                'price_centers': price_centers.tolist(),
                'volumes': volumes.tolist()
            }
        except Exception as e:
            logger.error(f"❌ [VolumeProfile] 计算标准价值区间失败: {e}")
            return None
