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

            # 3. 🌟 智能寻峰 (HVN)
            mean_vol = np.mean(smoothed_volumes)
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
