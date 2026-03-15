from typing import Dict, Optional

from src.strategy.triplea.signal_generator import TripleASignalGenerator


class ResearchTripleASignalGenerator(TripleASignalGenerator):
    """影子引擎专用信号生成器，添加完整的时间戳记录功能"""

    def __init__(self, symbol: str = "ETH-USDT-SWAP"):
        super().__init__(symbol)
        # 添加时间戳跟踪字段
        self.timestamp_tracker = {
            "a1_start_time": 0.0,  # A1开始时间
            "a1_end_time": 0.0,  # A1结束时间 (A2开始时间)
            "a2_start_time": 0.0,  # A2开始时间
            "a2_end_time": 0.0,  # A2结束时间 (A3开始时间)
            "a3_start_time": 0.0,  # A3开始时间
            "a3_end_time": 0.0,  # A3结束时间 (入场时间)
            "entry_time": 0.0  # 入场时间
        }
        self._current_tick_time = 0.0  # 当前tick的时间戳

    def process_tick(self, tick: Dict) -> Optional[Dict]:
        """重写：在调用父类前记录当前时间，用于时间戳记录"""
        self._current_tick_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0
        return super().process_tick(tick)

    def _handle_idle(self, price: float) -> Optional[Dict]:
        """重写：进入A1时记录开始时间"""
        result = super()._handle_idle(price)
        if self.status == "A1_WAIT_ABSORPTION":
            self.timestamp_tracker["a1_start_time"] = self._current_tick_time
        return result

    def _handle_absorption(self, price: float, current_time: float) -> Optional[Dict]:
        """重写：进入A2时记录A1结束和A2开始时间"""
        result = super()._handle_absorption(price, current_time)
        if self.status == "A2_WAIT_ACCUMULATION":
            self.timestamp_tracker["a1_end_time"] = current_time
            self.timestamp_tracker["a2_start_time"] = current_time
        return result

    def _handle_accumulation(self, price: float, current_time: float) -> Optional[Dict]:
        """重写：进入A3时记录A2结束和A3开始时间"""
        result = super()._handle_accumulation(price, current_time)
        if self.status == "A3_WAIT_AGGRESSION":
            self.timestamp_tracker["a2_end_time"] = current_time
            self.timestamp_tracker["a3_start_time"] = current_time
        return result

    def _handle_aggression(self, tick: Dict) -> Optional[Dict]:
        """重写：生成信号时记录A3结束和入场时间，并返回增强数据"""
        result = super()._handle_aggression(tick)
        if result and result.get('reason') == "TRIPLE_A_COMPLETE":
            current_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0
            self.timestamp_tracker["a3_end_time"] = current_time
            self.timestamp_tracker["entry_time"] = current_time

            # 获取父类计算的数据
            price = tick['price']
            recent_vol = 0.0
            recent_cvd = 0.0
            lookback_sec = 1.5
            # 计算近期成交量（从父类复制逻辑或直接访问父类属性）
            for t in reversed(self.rolling_ticks):
                if current_time - t[0] <= lookback_sec:
                    recent_cvd += t[1]  # tick_delta
                    recent_vol += t[2]  # size
                else:
                    break

            # 扩展信号字典
            result.update({
                "timestamps": self.timestamp_tracker.copy(),
                "cvd_metrics": {
                    "global_cvd": self.global_cvd,
                    "global_volume": self.global_volume,
                    "delta_ratio": abs(self.global_cvd) / (self.global_volume + 1e-8),
                    "recent_vol": recent_vol,
                    "recent_cvd": recent_cvd,
                    "recent_delta_ratio": recent_cvd / (recent_vol + 1e-8)
                },
                "diagnostics": {
                    "current_box_size": self.current_box_size,
                    "vol_spike_threshold": self.vol_spike_threshold,
                    "delta_ratio_threshold": self.delta_ratio_threshold
                }
            })
        return result

    def _reset_to_idle(self):
        """重写：重置时清空时间戳记录"""
        super()._reset_to_idle()
        self.timestamp_tracker = {k: 0.0 for k in self.timestamp_tracker}
        self._current_tick_time = 0.0

    def _manage_position_by_tick(self, tick: Dict) -> Optional[Dict]:
        """重写：在飞行期间，持续追踪 MFE 和 MAE"""
        price = tick['price']

        # 初始化 MFE/MAE 追踪器
        if 'mfe_price' not in self.timestamp_tracker:
            self.timestamp_tracker['mfe_price'] = price
            self.timestamp_tracker['mae_price'] = price

        if self.status == "LONG":
            self.timestamp_tracker['mfe_price'] = max(self.timestamp_tracker['mfe_price'], price)
            self.timestamp_tracker['mae_price'] = min(self.timestamp_tracker['mae_price'], price)
        elif self.status == "SHORT":
            self.timestamp_tracker['mfe_price'] = min(self.timestamp_tracker['mfe_price'], price)
            self.timestamp_tracker['mae_price'] = max(self.timestamp_tracker['mae_price'], price)

        result = super()._manage_position_by_tick(tick)

        # 如果订单结束，计算距离并打包进信号
        if result and result.get('action') in ["CLOSE_LONG", "CLOSE_SHORT"]:
            entry_price = self.micro_tracker.get('absorption_price', price)  # 近似入场价
            if self.status == "LONG":
                mfe_dist = self.timestamp_tracker['mfe_price'] - entry_price
                mae_dist = entry_price - self.timestamp_tracker['mae_price']
            else:
                mfe_dist = entry_price - self.timestamp_tracker['mfe_price']
                mae_dist = self.timestamp_tracker['mae_price'] - entry_price

            result['mfe_distance'] = round(mfe_dist, 4)
            result['mae_distance'] = round(mae_dist, 4)

            # 清理状态
            self.timestamp_tracker.pop('mfe_price', None)
            self.timestamp_tracker.pop('mae_price', None)

        return result
