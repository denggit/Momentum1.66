import numpy as np
import pandas as pd
from scipy.signal import find_peaks


class VolumeProfileBuilder:
    def __init__(self, value_area_pct=0.70, bin_size=0.5):
        """
        value_area_pct: 价值区间包含的成交量比例 (默认 70%)
        bin_size: 分箱精度 (比如 0.5 USDT，对 ETH 来说是比较合理的颗粒度)
        """
        self.value_area_pct = value_area_pct
        self.bin_size = bin_size

    def _create_zone(self, center_price, zone_type):
        """内部辅助方法：根据不同阵地类型，赋予非对称的战区厚度 (直接取消 Halo 概念)"""
        # 🚀 按照战术属性分配不同的宽度百分比
        if zone_type == "POC":
            buffer_pct = 0.0015  # POC 最窄 (±0.15%)，纯磁铁区
        elif zone_type in ["VAH", "VAL"]:
            buffer_pct = 0.0025  # 宏观城墙最宽 (±0.25%)，容纳插针
        else:
            buffer_pct = 0.0020  # HVN 战壕标准宽 (±0.20%)

        buffer_price = center_price * buffer_pct

        return {
            "type": zone_type,
            "center": round(center_price, 4),
            "zone_high": round(center_price + buffer_price, 4),
            "zone_low": round(center_price - buffer_price, 4)
        }

    def _check_overlap(self, zone1, zone2):
        """内部辅助方法：检查两个框是否发生物理重叠"""
        return zone1['zone_low'] <= zone2['zone_high'] and zone1['zone_high'] >= zone2['zone_low']

    def _merge_zones(self, zone1, zone2, new_type):
        """内部辅助方法：将两个重叠的顶级框合并成一个超级框"""
        return {
            "type": new_type,
            "center": round((zone1['center'] + zone2['center']) / 2, 4),
            "zone_high": max(zone1['zone_high'], zone2['zone_high']),
            "zone_low": min(zone1['zone_low'], zone2['zone_low'])
        }

    def build_profile(self, df_1m: pd.DataFrame):
        if df_1m.empty:
            return None

        # ==========================================
        # 1. 基础构建与分配成交量
        # ==========================================
        min_price = df_1m['low'].min()
        max_price = df_1m['high'].max()

        num_bins = int(np.ceil((max_price - min_price) / self.bin_size))
        num_bins = max(1, num_bins)

        price_bins = np.array([min_price + i * self.bin_size for i in range(num_bins + 1)])
        volume_profile = np.zeros(num_bins)

        for _, row in df_1m.iterrows():
            start_idx = int((row['low'] - min_price) / self.bin_size)
            end_idx = int((row['high'] - min_price) / self.bin_size)

            start_idx = max(0, min(num_bins - 1, start_idx))
            end_idx = max(0, min(num_bins - 1, end_idx))

            num_bins_crossed = end_idx - start_idx + 1
            vol_per_bin = row['volume'] / num_bins_crossed
            volume_profile[start_idx:end_idx + 1] += vol_per_bin

        # ==========================================
        # 2. 寻找主节点 (POC, VAH, VAL) 的原始价格
        # ==========================================
        poc_idx = np.argmax(volume_profile)
        poc_price = price_bins[poc_idx] + (self.bin_size / 2)

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

        # ==========================================
        # 3. 实例化主节点框，并处理顶级冲突 (合并)
        # ==========================================
        poc_zone = self._create_zone(poc_price, "POC")
        vah_zone = self._create_zone(vah_price, "VAH")
        val_zone = self._create_zone(val_price, "VAL")

        # 冲突解决法则 3：顶级神仙打架 (VAH/VAL 撞 POC)
        if self._check_overlap(poc_zone, vah_zone):
            mega_zone = self._merge_zones(poc_zone, vah_zone, "MEGA_POC_VAH")
            poc_zone = mega_zone
            vah_zone = mega_zone  # 让两个对象指针指向同一个超级框

        if self._check_overlap(poc_zone, val_zone):
            mega_zone = self._merge_zones(poc_zone, val_zone, "MEGA_POC_VAL")
            poc_zone = mega_zone
            val_zone = mega_zone

        # ==========================================
        # 4. 寻找次级节点 (HVN)，并处理下级冲突 (排他与过滤)
        # ==========================================
        # 我们假设两个 HVN 之间至少需要隔开一定距离
        hvn_buffer_pct = 0.0020
        min_bins_distance = max(1, int((poc_price * hvn_buffer_pct * 2) / self.bin_size))

        # 🚀 V2.0 升级：将 prominence 从 0.1 提高到 0.4 或 0.5！
        # 意思是：山峰的成交量必须至少是最高峰(POC)的 40% 以上，否则视为散户对敲噪音
        peaks, _ = find_peaks(volume_profile,
                              prominence=np.max(volume_profile) * 0.4,
                              distance=min_bins_distance)

        valid_hvns = []
        for p in peaks:
            if p == poc_idx:
                continue

            center = price_bins[p] + (self.bin_size / 2)
            temp_hvn = self._create_zone(center, "HVN")
            temp_hvn["volume"] = volume_profile[p]

            # 冲突解决法则 2：下级服从上级
            if (self._check_overlap(temp_hvn, poc_zone) or
                    self._check_overlap(temp_hvn, vah_zone) or
                    self._check_overlap(temp_hvn, val_zone)):
                continue

            valid_hvns.append(temp_hvn)

        # 🚀 V2.0 升级：按成交量从大到小排序，并【只保留前 3 个最大】的 HVN！
        valid_hvns = sorted(valid_hvns, key=lambda x: x['volume'], reverse=True)[:3]

        # ==========================================
        # 5. 组装专供机器人引擎交易的清爽地图 (去重 + 排序)
        # ==========================================
        tradable_zones = []
        seen_ids = set()

        # 将所有有效框塞进列表。如果 VAH 和 POC 合并了，它们的 id() 是一样的，会被 set 去重
        for z in [vah_zone, poc_zone, val_zone] + valid_hvns:
            if id(z) not in seen_ids:
                tradable_zones.append(z)
                seen_ids.add(id(z))

        # 引擎最喜欢的数据结构：从上到下按价格排序的清晰阻力/支撑带
        tradable_zones = sorted(tradable_zones, key=lambda x: x['center'], reverse=True)

        # 🆕 【新增】：顺手计算这批 1 分钟 K 线的平均成交量
        # 假设你的 DataFrame 里包含 'volume' 或 'vol' 列
        avg_vol = df_1m['volume'].mean() if 'volume' in df_1m.columns else df_1m['vol'].mean()

        # ==========================================
        # 6. 终极输出返回
        # ==========================================
        return {
            "POC": poc_zone,
            "VAH": vah_zone,
            "VAL": val_zone,
            "HVNs": valid_hvns,
            "tradable_zones": tradable_zones,  # <--- 机器人执行逻辑请认准这个 Key！
            "avg_vol_1m": avg_vol,
            "metadata": {
                "bin_size_used": self.bin_size,
                "zone_widths_pct": {"POC": 0.0015, "HVN": 0.0020, "VAH_VAL": 0.0025},
                "total_volume": total_volume
            }
        }
