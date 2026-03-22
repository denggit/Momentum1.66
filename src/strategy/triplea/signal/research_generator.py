#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影子引擎专用信号生成器（适配v3.0状态机）
"""
import time
from typing import Dict, Optional

from src.strategy.triplea.signal.signal_generator import TripleASignalGenerator
from src.strategy.triplea.state_machine.state_machine import TripleAState
from src.utils.log import get_logger

logger = get_logger(__name__)


class ResearchTripleASignalGenerator(TripleASignalGenerator):
    """影子引擎专用信号生成器（适配v3.0状态机）

    继承自新的TripleASignalGenerator，添加完整的研究数据记录功能：
    1. 状态转换时间戳记录
    2. 阶段指标收集（A1/A2/A3对应状态机状态）
    3. MFE/MAE追踪
    4. 增强信号数据（供orchestrator写入CSV）
    """

    def __init__(self, symbol: str = "ETH-USDT-SWAP", account_size_usdt: float = 300.0):
        super().__init__(symbol, is_shadow=True, account_size_usdt=account_size_usdt)

        # 状态转换时间戳跟踪（适配v3.0 5状态模型）
        self.state_timestamps = {
            TripleAState.IDLE: 0.0,
            TripleAState.MONITORING: 0.0,
            TripleAState.CONFIRMED: 0.0,
            TripleAState.ACCUMULATING: 0.0,
            TripleAState.POSITION: 0.0
        }

        # 阶段指标跟踪（映射到状态机状态）
        self.stage_metrics = {
            "monitoring": {},  # 对应MONITORING状态（原A1类似）
            "confirmed": {},  # 对应CONFIRMED状态（原A2类似）
            "accumulating": {},  # 对应ACCUMULATING状态（原A3类似）
            "position": {}  # 对应POSITION状态
        }

        # 当前状态历史（用于计算持续时间）
        self.current_state_start_time = 0.0
        self.previous_state = TripleAState.IDLE

        # MFE/MAE追踪
        self.mfe_price = 0.0
        self.mae_price = 0.0
        self.entry_price = 0.0

        # 当前tick时间戳
        self._current_tick_time = 0.0

        logger.info(f"ResearchTripleASignalGenerator 初始化完成 (symbol={symbol})")

    async def process_tick(self, tick: Dict) -> Optional[Dict]:
        """重写：记录时间戳并跟踪状态转换"""
        # 记录当前tick时间戳
        self._current_tick_time = int(tick.get('ts', tick.get('timestamp', time.time() * 1000))) / 1000.0

        # 调用父类处理tick
        signal = await super().process_tick(tick)

        # 跟踪状态转换和记录指标
        self._track_state_transitions()
        self._update_mfe_mae(tick)

        # 如果父类生成信号，增强信号数据
        if signal:
            signal = self._enhance_signal_data(signal, tick)

        return signal

    def _track_state_transitions(self):
        """跟踪状态机状态转换并记录时间戳"""
        current_state = self.state_machine.context.current_state
        current_time = self._current_tick_time

        # 如果状态发生变化
        if current_state != self.previous_state:
            # 记录新状态的开始时间
            self.state_timestamps[current_state] = current_time
            self.current_state_start_time = current_time

            # 计算前一个状态的持续时间
            if self.previous_state != TripleAState.IDLE:
                duration = current_time - self.state_timestamps.get(self.previous_state, current_time)
                self._record_stage_metrics(self.previous_state, duration)

            # 更新前一个状态
            self.previous_state = current_state

            logger.debug(f"状态转换: {self.previous_state} -> {current_state} @ {current_time}")

    def _record_stage_metrics(self, state: TripleAState, duration: float):
        """记录阶段指标"""
        if state == TripleAState.MONITORING:
            self.stage_metrics["monitoring"] = {
                "duration_sec": duration,
                "lvn_center_price": self.state_machine.context.lvn_center_price,
                "lvn_width": self.state_machine.context.lvn_width
            }
        elif state == TripleAState.CONFIRMED:
            self.stage_metrics["confirmed"] = {
                "duration_sec": duration,
                "cvd_divergence_direction": self.state_machine.context.cvd_divergence_direction,
                "cvd_zscore": self.state_machine.context.cvd_statistics.get(60, {}).get('z_score', 0.0)
            }
        elif state == TripleAState.ACCUMULATING:
            self.stage_metrics["accumulating"] = {
                "duration_sec": duration,
                "volatility_compression": self.state_machine.context.volatility_compression_detected,
                "tick_density": self.state_machine.context.ticks_per_second,
                "ticks_in_compression": self.state_machine.context.ticks_in_compression
            }
        elif state == TripleAState.POSITION:
            # 持仓状态指标在开仓时记录
            pass

    def _update_mfe_mae(self, tick: Dict):
        """更新MFE/MAE追踪"""
        price = tick['price']

        # 如果处于持仓状态，初始化或更新MFE/MAE
        if self.state_machine.context.current_state == TripleAState.POSITION:
            if self.entry_price == 0.0:
                # 首次进入持仓状态，记录入场价
                self.entry_price = self.state_machine.context.entry_price
                self.mfe_price = price
                self.mae_price = price
            else:
                # 更新MFE/MAE
                if self.state_machine.context.trade_direction == "LONG":
                    self.mfe_price = max(self.mfe_price, price)
                    self.mae_price = min(self.mae_price, price)
                else:  # SHORT
                    self.mfe_price = min(self.mfe_price, price)
                    self.mae_price = max(self.mae_price, price)

    def _enhance_signal_data(self, signal: Dict, tick: Dict) -> Dict:
        """增强信号数据，添加研究指标"""
        enhanced_signal = signal.copy()

        # 添加时间戳
        enhanced_signal["entry_time_unix"] = self._current_tick_time

        # 添加阶段持续时间
        enhanced_signal.update({
            "monitoring_duration_sec": self.stage_metrics.get("monitoring", {}).get("duration_sec", 0.0),
            "confirmed_duration_sec": self.stage_metrics.get("confirmed", {}).get("duration_sec", 0.0),
            "accumulating_duration_sec": self.stage_metrics.get("accumulating", {}).get("duration_sec", 0.0)
        })

        # 添加阶段指标
        enhanced_signal["stage_metrics"] = self.stage_metrics.copy()

        # 添加MFE/MAE数据（如果处于持仓状态）
        if self.state_machine.context.current_state == TripleAState.POSITION:
            if self.entry_price > 0:
                if self.state_machine.context.trade_direction == "LONG":
                    mfe_dist = self.mfe_price - self.entry_price
                    mae_dist = self.entry_price - self.mae_price
                else:  # SHORT
                    mfe_dist = self.entry_price - self.mfe_price
                    mae_dist = self.mae_price - self.entry_price

                enhanced_signal.update({
                    "mfe_distance": round(mfe_dist, 4),
                    "mae_distance": round(mae_dist, 4),
                    "mfe_price": self.mfe_price,
                    "mae_price": self.mae_price
                })


        return enhanced_signal

    def _reset_to_idle(self):
        """重写：重置研究数据"""
        super()._reset_to_idle()

        # 重置研究数据
        self.state_timestamps = {state: 0.0 for state in TripleAState}
        self.stage_metrics = {
            "monitoring": {},
            "confirmed": {},
            "accumulating": {},
            "position": {}
        }
        self.previous_state = TripleAState.IDLE
        self.current_state_start_time = 0.0

        # 重置MFE/MAE追踪
        self.mfe_price = 0.0
        self.mae_price = 0.0
        self.entry_price = 0.0

        logger.debug("研究数据已重置")
