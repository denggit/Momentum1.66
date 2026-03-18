from typing import Dict, Optional

from src.strategy.triplea.signal_generator import TripleASignalGenerator


class ResearchTripleASignalGenerator(TripleASignalGenerator):
    """影子引擎专用信号生成器，添加完整的时间戳记录功能"""

    def __init__(self, symbol: str = "ETH-USDT-SWAP"):
        super().__init__(symbol, is_shadow=True)
        # 添加时间戳跟踪字段 (精简版，只记录开始时间和持续时间)
        self.timestamp_tracker = {
            "a1_start_time": 0.0,  # A1开始时间
            "a2_start_time": 0.0,  # A2开始时间
            "entry_time": 0.0  # 入场时间
        }
        # 🆕 阶段指标跟踪 (精简版)
        self.stage_metrics = {
            "a1": {},  # A1阶段指标
            "a2": {},  # A2阶段指标
            "a3": {}  # A3阶段指标 (只记录触发时的状态)
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
        """重写：进入A2时记录A2开始时间，并保存A1阶段指标（包括持续时间）"""
        result = super()._handle_absorption(price, current_time)
        if self.status == "A2_WAIT_ACCUMULATION":
            # 计算A1持续时间
            a1_start_time = self.timestamp_tracker.get("a1_start_time", 0.0)
            a1_duration = current_time - a1_start_time if a1_start_time > 0 else 0.0

            self.timestamp_tracker["a2_start_time"] = current_time

            # 🆕 保存A1阶段指标（简化版，不依赖网格系统）
            # 新算法实现后将填充具体指标
            self.stage_metrics["a1"] = {
                "duration_sec": a1_duration,
                "current_cvd_15s": self.current_cvd_15s,
                "cvd_hourly_avg": self.cvd_hourly_avg
            }
            # 同时记录A2开始时的基准值（用于计算A2阶段变化）
            self.stage_metrics["a2_start"] = {
                "timestamp": current_time
            }
        return result

    def _handle_accumulation(self, price: float, current_time: float) -> Optional[Dict]:
        """重写：进入A3时记录A2持续时间，并保存A2阶段指标"""
        result = super()._handle_accumulation(price, current_time)
        if self.status == "A3_WAIT_AGGRESSION":
            # 计算A2持续时间
            a2_start_time = self.timestamp_tracker.get("a2_start_time", 0.0)
            a2_duration = current_time - a2_start_time if a2_start_time > 0 else 0.0

            # 🆕 保存A2阶段指标（精简版，只记录持续时间）
            # 新算法实现后将填充具体指标
            self.stage_metrics["a2"] = {
                "duration_sec": a2_duration
            }
        return result

    def _handle_aggression(self, tick: Dict) -> Optional[Dict]:
        """重写：生成信号时记录入场时间，并返回精简版增强数据"""
        result = super()._handle_aggression(tick)
        if result and result.get('reason') == "TRIPLE_A_COMPLETE":
            current_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0
            self.timestamp_tracker["entry_time"] = current_time

            # 获取父类计算的数据
            price = tick['price']
            # rolling_ticks已移除，近期成交量数据暂不可用
            recent_vol = 0.0
            recent_cvd = 0.0
            recent_delta_ratio = 0.0

            # 🆕 获取目标区域信息和POC价格
            target_zone_high = 0.0
            target_zone_low = 0.0
            macro_poc_price = 0.0
            distance_to_poc = 0.0

            if self.target_zone:
                target_zone_high = self.target_zone.get('zone_high', 0.0)
                target_zone_low = self.target_zone.get('zone_low', 0.0)

            if self.profile and 'POC' in self.profile:
                macro_poc_price = self.profile['POC'].get('center', 0.0)
                distance_to_poc = abs(price - macro_poc_price)

            # 保存A3阶段指标（精简版：只记录触发时的状态）
            self.stage_metrics["a3"] = {
                "current_cvd_15s": self.current_cvd_15s,
                "recent_vol": recent_vol,
                "recent_cvd": recent_cvd,
                "recent_delta_ratio": recent_delta_ratio
            }

            # 扩展信号字典（精简版）
            result.update({
                "entry_time_unix": current_time,
                "a1_duration_sec": self.stage_metrics.get("a1", {}).get("duration_sec", 0.0),
                "a2_duration_sec": self.stage_metrics.get("a2", {}).get("duration_sec", 0.0),
                "target_zone_high": target_zone_high,
                "target_zone_low": target_zone_low,
                "macro_poc_price": macro_poc_price,
                "distance_to_poc": distance_to_poc,
                # 🆕 添加阶段指标（精简版）
                "stage_metrics": {
                    "a1": self.stage_metrics.get("a1", {}),
                    "a2": self.stage_metrics.get("a2", {}),
                    "a3": self.stage_metrics.get("a3", {})
                }
            })
        return result

    def _reset_to_idle(self):
        """重写：重置时清空时间戳记录和阶段指标"""
        super()._reset_to_idle()
        self.timestamp_tracker = {
            "a1_start_time": 0.0,
            "a2_start_time": 0.0,
            "entry_time": 0.0
        }
        self.stage_metrics = {
            "a1": {},
            "a2": {},
            "a3": {}
        }
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

        # 🚀 核心修复：在调用父类之前，先把当前状态和极值"快照"保存到局部变量中！
        # 因为一旦调用 super() 触发撞线，父类会调用 _reset_to_idle，提前把 self.timestamp_tracker 清空。
        current_status = self.status
        current_mfe = self.timestamp_tracker['mfe_price']
        current_mae = self.timestamp_tracker['mae_price']

        # 调用父类逻辑
        result = super()._manage_position_by_tick(tick)

        # 如果订单结束，使用提前保存的快照变量计算距离
        if result and result.get('action') in ["CLOSE_LONG", "CLOSE_SHORT"]:
            entry_price = self.micro_tracker.get('entry_price', price)  # 近似入场价

            if current_status == "LONG":
                mfe_dist = current_mfe - entry_price
                mae_dist = entry_price - current_mae
            else:  # SHORT
                mfe_dist = entry_price - current_mfe
                mae_dist = current_mae - entry_price

            result['mfe_distance'] = round(mfe_dist, 4)
            result['mae_distance'] = round(mae_dist, 4)

        return result
