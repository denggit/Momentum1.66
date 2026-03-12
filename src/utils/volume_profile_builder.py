import numpy as np
import pandas as pd
from scipy.signal import find_peaks


class FabioProfileBuilder:
    def __init__(self, value_area_pct=0.70, bin_size=0.5, zone_pct=0.002):
        """
        value_area_pct: 价值区间包含的成交量比例 (默认 70%)
        bin_size: 分箱精度 (比如 0.5 USDT，对 ETH 来说是比较合理的颗粒度)
        zone_pct: 框的单侧容错百分比，默认 0.002 (即 0.2%)
                  例如 ETH 在 3000 U 时，单侧宽 6 U，总宽度 12 U 的流动性区间
        """
        self.value_area_pct = value_area_pct
        self.bin_size = bin_size
        self.zone_pct = zone_pct

    def _create_zone(self, center_price, zone_type):
        """内部辅助方法：将一根价格线根据动态百分比转换为一个可交易的框 (Zone)"""
        # 动态计算当前价格点对应的绝对缓冲宽度
        buffer_price = center_price * self.zone_pct

        return {
            "type": zone_type,
            "center": round(center_price, 4),
            "zone_high": round(center_price + buffer_price, 4),
            "zone_low": round(center_price - buffer_price, 4)
        }

    def build_profile(self, df_1m: pd.DataFrame):
        if df_1m.empty:
            return None

        # 1. 获取高低点与动态计算 Bins
        min_price = df_1m['low'].min()
        max_price = df_1m['high'].max()

        num_bins = int(np.ceil((max_price - min_price) / self.bin_size))
        num_bins = max(1, num_bins)  # 保底防御，避免被除数为0

        price_bins = np.array([min_price + i * self.bin_size for i in range(num_bins + 1)])
        volume_profile = np.zeros(num_bins)

        # 2. 分配成交量
        for _, row in df_1m.iterrows():
            start_idx = int((row['low'] - min_price) / self.bin_size)
            end_idx = int((row['high'] - min_price) / self.bin_size)

            start_idx = max(0, min(num_bins - 1, start_idx))
            end_idx = max(0, min(num_bins - 1, end_idx))

            num_bins_crossed = end_idx - start_idx + 1
            vol_per_bin = row['volume'] / num_bins_crossed
            volume_profile[start_idx:end_idx + 1] += vol_per_bin

        # 3. 寻找主 POC
        poc_idx = np.argmax(volume_profile)
        poc_price = price_bins[poc_idx] + (self.bin_size / 2)

        # 4. 计算 VAH 和 VAL
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

        vah_price = price_bins[upper_idx + 1]
        val_price = price_bins[lower_idx]

        # 5. 寻找次级 HVN
        peaks, _ = find_peaks(volume_profile, prominence=np.max(volume_profile) * 0.1)
        hvns = []
        for p in peaks:
            if p != poc_idx:
                center = price_bins[p] + (self.bin_size / 2)
                hvn_zone = self._create_zone(center, "HVN")
                hvn_zone["volume"] = volume_profile[p]  # HVN 独有属性，保留成交量以便排序
                hvns.append(hvn_zone)

        # 按成交量从大到小对 HVN 排序
        hvns = sorted(hvns, key=lambda x: x['volume'], reverse=True)

        # 6. 终极输出：直接吐出包装好的动态“框 (Zones)”
        return {
            "VAH": self._create_zone(vah_price, "VAH"),
            "VAL": self._create_zone(val_price, "VAL"),
            "POC": self._create_zone(poc_price, "POC"),
            "HVNs": hvns,
            "metadata": {
                "bin_size_used": self.bin_size,
                "zone_pct_used": self.zone_pct,  # 记录当前使用的百分比
                "total_volume": total_volume
            }
        }