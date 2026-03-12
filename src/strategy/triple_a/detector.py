#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Triple-A 模型检测器
检测Absorption（吸收）、Accumulation（累积）、Aggression（侵略）三个阶段
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Tuple
import numpy as np

from src.strategy.triple_a.config import TripleAConfig
from src.strategy.triple_a.value_area_analyzer import ValueAreaAnalyzer
from src.strategy.triple_a.orderflow_validator import OrderFlowValidator
from src.strategy.triple_a.market_environment import MarketEnvironmentAnalyzer
from src.strategy.orderflow.smc_validator import SMCValidator
from src.context.market_context import MarketContext
from src.utils.log import get_logger

logger = get_logger(__name__)


@dataclass
class TripleAState:
    """Triple-A状态数据类"""
    current_state: str = "IDLE"  # IDLE, ABSORPTION_DETECTED, ACCUMULATION_CONFIRMED, AGGRESSION_TRIGGERED
    absorption_start_time: float = 0.0
    accumulation_start_time: float = 0.0
    aggression_start_time: float = 0.0

    # 价格区间
    absorption_price: float = 0.0
    accumulation_low: float = 0.0
    accumulation_high: float = 0.0

    # 检测得分
    absorption_score: float = 0.0
    accumulation_score: float = 0.0
    aggression_score: float = 0.0

    # 数据缓存
    recent_ticks: List[Dict[str, Any]] = field(default_factory=list)
    recent_volumes: List[float] = field(default_factory=list)

    def reset(self):
        """重置状态"""
        self.__init__()


class TripleADetector:
    """Triple-A 模型检测器"""

    def __init__(self, config: TripleAConfig, context: MarketContext):
        self.config = config
        self.context = context
        self.state = TripleAState()

        # 统计数据
        self.stats = {
            "absorption_signals": 0,
            "accumulation_signals": 0,
            "aggression_signals": 0,
            "failed_auctions": 0,
            "total_ticks": 0,
            "value_area_validations": 0,
            "orderflow_validations": 0,
            "multi_tf_validations": 0,
            "validation_passed": 0,
            "validation_failed": 0
        }

        # 时间窗口缓存
        self.tick_window = []
        self.max_window_size = 1000  # 最多存储1000个tick

        # Fabio验证器初始化
        self.value_area_analyzer = None
        self.smc_validator = None
        self._initialize_validators()

        logger.info(f"🚀 Triple-A检测器初始化完成 (Fabio验证: {self.config.value_area_validation_enabled})")

    def _initialize_validators(self):
        """初始化Fabio验证器"""
        try:
            # 价值区间分析器
            if self.config.value_area_validation_enabled:
                self.value_area_analyzer = ValueAreaAnalyzer(
                    bin_size=0.5,
                    balance_range_pct=self.config.value_area_balance_range_pct
                )
                logger.info(f"✅ 价值区间验证器初始化完成")

            # 订单流验证器
            if self.config.orderflow_validation_enabled:
                self.orderflow_validator = OrderFlowValidator(
                    cvd_threshold=self.config.orderflow_cvd_threshold,
                    large_order_ratio=self.config.orderflow_large_order_ratio
                )
                logger.info(f"✅ 订单流验证器初始化完成")

            # SMC多时间框架验证器
            if self.config.multi_tf_alignment_enabled:
                self.smc_validator = SMCValidator(
                    symbol=self.config.symbol,
                    timeframes=self.config.multi_tf_timeframes
                )
                logger.info(f"✅ SMC多时间框架验证器初始化完成")

            # 市场环境分析器
            if self.config.adaptive_validation_enabled:
                self.market_environment_analyzer = MarketEnvironmentAnalyzer(
                    volatility_threshold_low=self.config.market_volatility_threshold_low,
                    volatility_threshold_high=self.config.market_volatility_threshold_high
                )
                logger.info(f"✅ 市场环境分析器初始化完成")

        except Exception as e:
            logger.error(f"❌ 验证器初始化失败: {e}")

    async def process_tick(self, tick: dict) -> Optional[dict]:
        """
        处理tick数据，检测Triple-A模式

        返回:
            Triple-A信号字典，包含阶段和置信度
        """
        self.stats["total_ticks"] += 1

        # 1. 更新数据窗口
        self._update_tick_window(tick)

        # 2. 根据当前状态执行检测
        signal = None

        if self.state.current_state == "IDLE":
            signal = await self._detect_absorption(tick)
        elif self.state.current_state == "ABSORPTION_DETECTED":
            signal = await self._detect_accumulation(tick)
        elif self.state.current_state == "ACCUMULATION_CONFIRMED":
            signal = await self._detect_aggression(tick)
        elif self.state.current_state == "AGGRESSION_TRIGGERED":
            # 在Aggression触发后，继续监控Failed Auction
            signal = await self._monitor_failed_auction(tick)

        # 3. 记录信号（如果有）
        if signal:
            await self._record_signal(signal, tick)

        return signal

    async def _detect_absorption(self, tick: dict) -> Optional[dict]:
        """
        检测Absorption（吸收）阶段

        检测条件：
        1. 价格在关键水平 ± absorption_price_threshold 范围内波动
        2. 出现异常大单（>平均成交量 * absorption_volume_ratio）但价格未突破
        3. 买量/卖量比率异常但价格稳定
        4. 持续至少absorption_window_seconds秒
        """
        if len(self.tick_window) < 10:
            return None

        # 计算关键水平（使用当前价格作为参考）
        current_price = tick.get('price', 0.0)
        if current_price <= 0:
            return None

        # 计算平均成交量
        valid_volumes = [t.get('size', 0) for t in self.tick_window[-100:] if t.get('size', 0) > 0]
        avg_volume = np.mean(valid_volumes) if valid_volumes else 0.0
        if avg_volume <= 0:
            return None

        # 计算价格稳定性
        recent_prices = [t.get('price', 0.0) for t in self.tick_window[-30:] if t.get('price', 0.0) > 0]
        if len(recent_prices) < 10:
            return None

        price_range = (max(recent_prices) - min(recent_prices)) / min(recent_prices)
        price_stable = price_range < self.config.absorption_price_threshold

        # 检测异常大单
        current_volume = tick.get('size', 0)
        large_order = current_volume > avg_volume * self.config.absorption_volume_ratio

        # 计算买量/卖量比率
        # 从tick数据中提取买卖方向
        recent_sides = [t.get('side', '') for t in self.tick_window[-30:]]
        buy_count = sum(1 for s in recent_sides if s == 'buy')
        sell_count = sum(1 for s in recent_sides if s == 'sell')
        total_count = buy_count + sell_count

        if total_count > 0:
            buy_sell_ratio = buy_count / sell_count if sell_count > 0 else float('inf')
        else:
            buy_sell_ratio = 1.0

        # 计算吸收得分
        absorption_score = self._calculate_absorption_score(
            price_stable, large_order, buy_sell_ratio, len(self.tick_window)
        )

        self.state.absorption_score = absorption_score

        # 检查是否达到阈值
        if absorption_score >= self.config.absorption_score_threshold:
            # 确认吸收阶段
            self.state.current_state = "ABSORPTION_DETECTED"
            self.state.absorption_start_time = time.time()
            self.state.absorption_price = current_price

            logger.info(f"🎯 Absorption检测到！得分: {absorption_score:.2f}, 价格: {current_price:.2f}")

            return {
                "type": "ABSORPTION_DETECTED",
                "phase": "absorption",
                "score": absorption_score,
                "price": current_price,
                "timestamp": tick.get('ts', time.time()),
                "absorption_price": current_price
            }

        return None

    async def _detect_accumulation(self, tick: dict) -> Optional[dict]:
        """
        检测Accumulation（累积）阶段

        检测条件：
        1. 价格在窄幅区间整理（振幅 < accumulation_width_pct）
        2. 成交量逐渐萎缩
        3. 形成订单块（价格多次测试同一水平）
        4. Absorption后持续至少accumulation_window_seconds秒
        """
        current_time = time.time()
        time_since_absorption = current_time - self.state.absorption_start_time

        # 确保有足够的时间
        if time_since_absorption < self.config.absorption_window_seconds:
            return None

        current_price = tick.get('price', 0.0)

        # 更新累积区间
        if self.state.accumulation_low == 0:
            # 初始化累积区间，设置一个最小宽度（当前价格的0.1%）
            min_width = current_price * 0.001  # 0.1%
            self.state.accumulation_low = current_price - min_width / 2
            self.state.accumulation_high = current_price + min_width / 2
        else:
            self.state.accumulation_low = min(self.state.accumulation_low, current_price)
            self.state.accumulation_high = max(self.state.accumulation_high, current_price)

            # 确保累积区间有最小宽度（避免宽度为0）
            min_width_pct = 0.0005  # 0.05%最小宽度
            min_width = current_price * min_width_pct
            current_width = self.state.accumulation_high - self.state.accumulation_low
            if current_width < min_width:
                # 扩展区间到最小宽度，以当前价格为中心
                self.state.accumulation_low = current_price - min_width / 2
                self.state.accumulation_high = current_price + min_width / 2

        # 计算价格范围
        price_range = (self.state.accumulation_high - self.state.accumulation_low) / self.state.accumulation_low
        price_range_ok = price_range < self.config.accumulation_width_pct

        # 计算成交量萎缩
        volume_declining = self._check_volume_declining()

        # 检查订单块形成（价格测试次数）
        touch_count = self._count_price_touches(current_price)

        # 计算累积得分
        accumulation_score = self._calculate_accumulation_score(
            price_range_ok, volume_declining, touch_count, time_since_absorption
        )

        self.state.accumulation_score = accumulation_score

        # 检查是否达到阈值
        if accumulation_score >= self.config.accumulation_score_threshold:
            # 确认累积阶段
            self.state.current_state = "ACCUMULATION_CONFIRMED"
            self.state.accumulation_start_time = current_time

            logger.info(f"📊 Accumulation确认！得分: {accumulation_score:.2f}, "
                       f"区间: [{self.state.accumulation_low:.2f}, {self.state.accumulation_high:.2f}]")

            return {
                "type": "ACCUMULATION_CONFIRMED",
                "phase": "accumulation",
                "score": accumulation_score,
                "price": current_price,
                "timestamp": tick.get('ts', time.time()),
                "accumulation_low": self.state.accumulation_low,
                "accumulation_high": self.state.accumulation_high,
                "time_since_absorption": time_since_absorption
            }

        # 如果累积检测超时，返回IDLE状态
        if time_since_absorption > self.config.accumulation_window_seconds * 2:
            logger.info("⏰ Accumulation检测超时，返回IDLE状态")
            self.state.reset()

        return None

    async def _detect_aggression(self, tick: dict) -> Optional[dict]:
        """
        检测Aggression（侵略）阶段

        检测条件：
        1. 成交量爆发（>平均成交量 * aggression_volume_spike）
        2. 价格突破累积区间边界 ± aggression_breakout_pct
        3. 价格变化速度突然增加
        4. 突破方向与订单流方向一致
        """
        current_price = tick.get('price', 0.0)
        current_volume = tick.get('size', 0)

        # 计算平均成交量
        valid_volumes = [t.get('size', 0) for t in self.tick_window[-100:] if t.get('size', 0) > 0]
        avg_volume = np.mean(valid_volumes) if valid_volumes else 0.0
        if avg_volume <= 0:
            return None

        # 检查成交量爆发
        volume_spike = current_volume > avg_volume * self.config.aggression_volume_spike

        # 检查价格突破
        breakout_up = current_price > self.state.accumulation_high * (1 + self.config.aggression_breakout_pct)
        breakout_down = current_price < self.state.accumulation_low * (1 - self.config.aggression_breakout_pct)
        breakout_detected = breakout_up or breakout_down

        # 计算价格速度
        velocity_spike = self._check_velocity_spike(current_price)

        # 检查订单流方向（简化版）
        orderflow_aligned = self._check_orderflow_alignment(breakout_up, breakout_down, tick)

        # 计算侵略得分
        aggression_score = self._calculate_aggression_score(
            volume_spike, breakout_detected, velocity_spike, orderflow_aligned
        )

        self.state.aggression_score = aggression_score

        # 检查是否达到阈值
        if aggression_score >= self.config.aggression_score_threshold:
            # 确认侵略阶段
            self.state.current_state = "AGGRESSION_TRIGGERED"
            self.state.aggression_start_time = time.time()

            # 首先基于明确的突破方向判断
            if breakout_up:
                direction = "UP"
            elif breakout_down:
                direction = "DOWN"
            else:
                # 如果没有明确的突破方向，尝试基于订单流趋势判断
                orderflow_direction = self._get_orderflow_direction(tick)
                if orderflow_direction in ["UP", "DOWN"]:
                    direction = orderflow_direction
                    logger.debug(f"📊 基于订单流趋势确定方向: {direction}")
                else:
                    # 方向不明确，不触发Aggression信号
                    logger.debug(f"⚠️ Aggression得分达到阈值{aggression_score:.2f}但方向不明确，不触发信号")
                    return None

            logger.warning(f"🚨 Aggression触发！得分: {aggression_score:.2f}, "
                          f"方向: {direction}, 价格: {current_price:.2f}")

            # 构建基础信号
            signal = {
                "type": "AGGRESSION_TRIGGERED",
                "phase": "aggression",
                "score": aggression_score,
                "price": current_price,
                "timestamp": tick.get('ts', time.time()),
                "direction": direction,
                "breakout_price": current_price,
                "volume_spike_ratio": current_volume / avg_volume if avg_volume > 0 else 0,
                "accumulation_low": self.state.accumulation_low,
                "accumulation_high": self.state.accumulation_high
            }

            # Fabio验证阶段1：价值区间验证
            validation_results = {}

            if self.config.value_area_validation_enabled:
                is_valid_va, validation_msg_va = self._validate_with_value_area(signal, tick)
                validation_results['value_area'] = {
                    'valid': is_valid_va,
                    'message': validation_msg_va
                }
                if not is_valid_va:
                    logger.warning(f"⛔ Aggression信号被价值区间验证拒绝: {validation_msg_va}")
                    # 验证失败，不返回信号
                    return None

            # Fabio验证阶段2：订单流验证
            if self.config.orderflow_validation_enabled:
                is_valid_of, validation_msg_of = self._validate_with_orderflow(signal, tick)
                validation_results['orderflow'] = {
                    'valid': is_valid_of,
                    'message': validation_msg_of
                }
                if not is_valid_of:
                    logger.warning(f"⛔ Aggression信号被订单流验证拒绝: {validation_msg_of}")
                    # 验证失败，不返回信号
                    return None

            # Fabio验证阶段3：多时间框架验证
            if self.config.multi_tf_alignment_enabled:
                is_valid_mtf, validation_msg_mtf = self._validate_with_multi_timeframe(signal, tick)
                validation_results['multi_timeframe'] = {
                    'valid': is_valid_mtf,
                    'message': validation_msg_mtf
                }
                if not is_valid_mtf:
                    logger.warning(f"⛔ Aggression信号被多时间框架验证拒绝: {validation_msg_mtf}")
                    # 验证失败，不返回信号
                    return None

            # 添加验证信息到信号
            if validation_results:
                signal['validation'] = validation_results

            logger.info(f"✅ Aggression信号通过验证: {validation_msg if 'validation_msg' in locals() else '基础验证'}")

            return signal

        # 如果Aggression检测超时，返回IDLE状态
        time_since_accumulation = time.time() - self.state.accumulation_start_time
        if time_since_accumulation > self.config.accumulation_window_seconds:
            logger.info("⏰ Aggression检测超时，返回IDLE状态")
            self.state.reset()

        return None

    async def _monitor_failed_auction(self, tick: dict) -> Optional[dict]:
        """
        监控Failed Auction（失败拍卖）

        检测条件：
        1. Aggression触发后failed_auction_window_seconds秒内
        2. 价格重新进入Accumulation区间
        3. 回归时成交量放大
        4. CVD方向与突破方向相反
        """
        current_time = time.time()
        time_since_aggression = current_time - self.state.aggression_start_time

        # 检查时间窗口
        if time_since_aggression > self.config.failed_auction_window_seconds:
            # 超时，返回IDLE状态
            logger.info("⏰ Failed Auction监控超时，返回IDLE状态")
            self.state.reset()
            return None

        current_price = tick.get('price', 0.0)

        # 检查价格是否回归累积区间
        price_back_in_range = (
            self.state.accumulation_low <= current_price <= self.state.accumulation_high
        )

        if not price_back_in_range:
            return None

        # 检查成交量确认
        valid_volumes = [t.get('size', 0) for t in self.tick_window[-100:] if t.get('size', 0) > 0]
        avg_volume = np.mean(valid_volumes) if valid_volumes else 0.0
        current_volume = tick.get('size', 0)
        volume_confirmation = current_volume > avg_volume * self.config.failed_auction_volume_confirmation_multiplier

        # 检查订单流反转（简化版）
        orderflow_reversal = self._check_orderflow_reversal(tick)

        # 计算Failed Auction得分
        failed_auction_score = self._calculate_failed_auction_score(
            time_since_aggression, price_back_in_range, volume_confirmation, orderflow_reversal
        )

        # 检查是否达到阈值
        if failed_auction_score >= self.config.failed_auction_detection_threshold:
            self.stats["failed_auctions"] += 1

            logger.error(f"💥 Failed Auction检测到！得分: {failed_auction_score:.2f}, "
                        f"价格回归累积区间: {current_price:.2f}")

            # 生成Failed Auction信号
            signal = {
                "type": "FAILED_AUCTION_DETECTED",
                "phase": "failed_auction",
                "score": failed_auction_score,
                "price": current_price,
                "timestamp": tick.get('ts', time.time()),
                "time_since_aggression": time_since_aggression,
                "volume_confirmation": volume_confirmation,
                "accumulation_low": self.state.accumulation_low,
                "accumulation_high": self.state.accumulation_high
            }

            # 重置状态
            self.state.reset()

            return signal

        return None

    def _calculate_absorption_score(self, price_stable: bool, large_order: bool,
                                   buy_sell_ratio: float, tick_count: int) -> float:
        """计算Absorption得分"""
        score = 0.0

        # 价格稳定性权重
        if price_stable:
            score += 0.25

        # 大单吸收权重
        if large_order:
            score += 0.3

        # 买量/卖量比率权重（简化）
        if 1.5 < buy_sell_ratio < 3.0:
            score += 0.3

        # 时间持续性权重
        if tick_count > 50:
            score += 0.15

        return min(score, 1.0)

    def _calculate_accumulation_score(self, price_range_ok: bool, volume_declining: bool,
                                     touch_count: int, time_since_absorption: float) -> float:
        """计算Accumulation得分"""
        score = 0.0

        # 价格范围权重
        if price_range_ok:
            score += 0.4

        # 成交量萎缩权重
        if volume_declining:
            score += 0.3

        # 订单块形成权重
        if touch_count >= 3:
            score += 0.2

        # 时间持续性权重
        min_time = self.config.absorption_window_seconds
        if time_since_absorption >= min_time:
            time_score = min(time_since_absorption / (min_time * 2), 1.0)
            score += time_score * 0.1

        return min(score, 1.0)

    def _calculate_aggression_score(self, volume_spike: bool, breakout_detected: bool,
                                   velocity_spike: bool, orderflow_aligned: bool) -> float:
        """计算Aggression得分"""
        score = 0.0

        # 成交量爆发权重
        if volume_spike:
            score += 0.35

        # 价格突破权重
        if breakout_detected:
            score += 0.30

        # 速度加速权重
        if velocity_spike:
            score += 0.20

        # 订单流方向权重
        if orderflow_aligned:
            score += 0.15

        return min(score, 1.0)

    def _calculate_failed_auction_score(self, time_since_aggression: float,
                                       price_back_in_range: bool,
                                       volume_confirmation: bool,
                                       orderflow_reversal: bool) -> float:
        """计算Failed Auction得分"""
        score = 0.0

        # 时间窗口权重（越早回归得分越高）
        max_window = self.config.failed_auction_window_seconds
        time_score = 1.0 - min(time_since_aggression / max_window, 1.0)
        score += time_score * 0.4

        # 价格回归权重
        if price_back_in_range:
            score += 0.3

        # 成交量确认权重
        if volume_confirmation:
            score += 0.2

        # 订单流反转权重
        if orderflow_reversal:
            score += 0.1

        return min(score, 1.0)

    def _update_tick_window(self, tick: dict):
        """更新tick窗口"""
        self.tick_window.append(tick.copy())
        if len(self.tick_window) > self.max_window_size:
            self.tick_window.pop(0)

    def _check_volume_declining(self) -> bool:
        """检查成交量是否逐渐萎缩"""
        if len(self.tick_window) < 20:
            return False

        volumes = [t.get('size', 0) for t in self.tick_window[-20:]]

        # 简单检查：最近5个tick的成交量是否小于前5个
        if len(volumes) >= 10:
            recent_avg = np.mean(volumes[-5:])
            earlier_avg = np.mean(volumes[-10:-5])
            return recent_avg < earlier_avg * 0.8

        return False

    def _count_price_touches(self, current_price: float) -> int:
        """计算价格测试同一水平的次数"""
        if len(self.tick_window) < 10:
            return 0

        # 定义价格容忍度
        tolerance = current_price * 0.0005  # 0.05%

        # 计算在容忍度范围内接近当前价格的次数
        touch_count = 0
        for tick in self.tick_window[-30:]:
            price = tick.get('price', 0.0)
            if abs(price - current_price) / current_price <= tolerance:
                touch_count += 1

        return touch_count

    def _check_velocity_spike(self, current_price: float) -> bool:
        """检查价格变化速度是否突然增加"""
        if len(self.tick_window) < 10:
            return False

        # 计算最近的价格变化速度
        recent_prices = [t.get('price', 0.0) for t in self.tick_window[-10:]]
        if len(recent_prices) < 5:
            return False

        # 计算速度（价格变化百分比）
        velocity = abs((recent_prices[-1] - recent_prices[-5]) / recent_prices[-5])

        # 计算平均速度
        if len(self.tick_window) >= 50:
            all_prices = [t.get('price', 0.0) for t in self.tick_window[-50:]]
            avg_velocity = 0
            for i in range(5, len(all_prices)):
                v = abs((all_prices[i] - all_prices[i-5]) / all_prices[i-5])
                avg_velocity += v
            avg_velocity /= (len(all_prices) - 5)

            return velocity > avg_velocity * 2

        return False

    def _check_orderflow_alignment(self, breakout_up: bool, breakout_down: bool,
                                  tick: dict) -> bool:
        """
        检查订单流方向是否与突破方向一致

        第二阶段：集成OrderFlowValidator
        """
        if not self.config.orderflow_validation_enabled:
            return True

        try:
            # 如果有订单流验证器，使用它进行验证
            if hasattr(self, 'orderflow_validator') and self.orderflow_validator:
                # 构建测试信号
                direction = 'UP' if breakout_up else 'DOWN' if breakout_down else 'UNKNOWN'
                if direction == 'UNKNOWN':
                    return True

                test_signal = {
                    'direction': direction,
                    'price': tick.get('price', 0.0),
                    'breakout_price': tick.get('price', 0.0)
                }

                # 获取相关tick数据
                relevant_ticks = self.tick_window[-50:] if self.tick_window else [tick]

                # 使用订单流验证器
                is_valid, message = self.orderflow_validator.validate_aggression_with_orderflow(
                    test_signal, relevant_ticks
                )

                if not is_valid:
                    logger.debug(f"⚠️ 订单流方向不匹配: {message}")
                    return False

                return True
            else:
                # 没有验证器，使用简化验证
                current_price = tick.get('price', 0.0)
                side = tick.get('side', '')
                cvd = tick.get('cvd', None)

                if cvd is not None:
                    if breakout_up and cvd > 0:
                        return True
                    elif breakout_down and cvd < 0:
                        return True
                    else:
                        logger.debug(f"⚠️ 订单流方向不匹配: 突破方向{breakout_up}/{breakout_down}, CVD={cvd}")
                        return False

                if side:
                    if breakout_up and side == 'buy':
                        return True
                    elif breakout_down and side == 'sell':
                        return True

                logger.debug(f"ℹ️ 订单流信息不足，使用默认验证")
                return True

        except Exception as e:
            logger.error(f"❌ 订单流验证失败: {e}")
            return False

    def _get_orderflow_direction(self, tick: dict) -> str:
        """获取订单流趋势方向

        返回:
            'UP': 订单流显示上涨趋势
            'DOWN': 订单流显示下跌趋势
            'UNKNOWN': 无法确定方向
        """
        try:
            # 检查最近的tick数据判断趋势
            if len(self.tick_window) < 10:
                return "UNKNOWN"

            # 分析最近20个tick的买卖方向
            recent_sides = [t.get('side', '') for t in self.tick_window[-20:]]
            buy_count = sum(1 for s in recent_sides if s == 'buy')
            sell_count = sum(1 for s in recent_sides if s == 'sell')

            if buy_count + sell_count < 5:  # 数据不足
                return "UNKNOWN"

            # 计算买卖比例
            buy_ratio = buy_count / (buy_count + sell_count)

            if buy_ratio > 0.6:  # 买方占优
                return "UP"
            elif buy_ratio < 0.4:  # 卖方占优
                return "DOWN"
            else:  # 买卖平衡
                return "UNKNOWN"

        except Exception as e:
            logger.error(f"❌ 订单流方向判断失败: {e}")
            return "UNKNOWN"

    def _check_orderflow_reversal(self, tick: dict) -> bool:
        """检查订单流是否反转（简化版）"""
        # 实际应用中需要从tick数据中提取CVD方向变化
        # 这里返回True作为占位符
        return True

    def _validate_with_value_area(self, signal: dict, tick: dict) -> Tuple[bool, str]:
        """
        使用价值区间验证信号

        参数:
            signal: Triple-A信号
            tick: 当前tick数据

        返回:
            tuple: (验证结果, 验证消息)
        """
        if not self.config.value_area_validation_enabled or not self.value_area_analyzer:
            return True, "价值区间验证未启用"

        try:
            current_price = tick.get('price', 0.0)
            direction = signal.get('direction', '')
            breakout_price = signal.get('breakout_price', current_price)

            # 获取K线数据（需要从context或数据源获取）
            # 第一阶段：先使用简化验证
            # 第二阶段：从MarketContext获取真实K线数据

            # 检查是否有可用的K线数据
            if hasattr(self.context, 'get_historical_data'):
                df = self.context.get_historical_data(timeframe='5m', limit=200)
                if df is not None and not df.empty:
                    # 使用价值区间分析器计算Fabio平衡区间价值区间
                    value_area_result = self.value_area_analyzer.calculate_fabio_value_area(
                        df, current_price
                    )

                    if value_area_result:
                        self.stats["value_area_validations"] += 1

                        # 验证信号与价值区间的一致性
                        is_valid, message = self.value_area_analyzer.validate_aggression_with_value_area(
                            signal, value_area_result
                        )

                        # 分析价值区间强度
                        strength_result = self.value_area_analyzer.analyze_value_area_strength(value_area_result)
                        strength_score = strength_result.get('score', 0.0)

                        # 获取适应性验证参数
                        validation_params = self._get_adaptive_validation_params(tick)
                        min_strength_required = validation_params.get('min_value_area_strength', self.config.min_value_area_strength)

                        if strength_score < min_strength_required:
                            logger.warning(f"⚠️ 价值区间强度不足: {strength_score:.1f} < {min_strength_required}")
                            return False, f"价值区间强度不足 ({strength_score:.1f} < {min_strength_required})"

                        if is_valid:
                            self.stats["validation_passed"] += 1
                        else:
                            self.stats["validation_failed"] += 1

                        return is_valid, message

            # 如果没有K线数据或验证器未初始化，返回验证通过但记录警告
            logger.debug("ℹ️ 价值区间验证：无K线数据，跳过验证")
            return True, "无K线数据，跳过价值区间验证"

        except Exception as e:
            logger.error(f"❌ 价值区间验证失败: {e}")
            return False, f"验证异常: {str(e)}"

    def _validate_with_multi_timeframe(self, signal: dict, tick: dict) -> Tuple[bool, str]:
        """
        使用多时间框架结构验证信号

        参数:
            signal: Triple-A信号
            tick: 当前tick数据

        返回:
            tuple: (验证结果, 验证消息)
        """
        if not self.config.multi_tf_alignment_enabled or not self.smc_validator:
            return True, "多时间框架验证未启用"

        try:
            current_price = tick.get('price', 0.0)
            direction = signal.get('direction', '')

            # 更新SMC验证器结构
            self.smc_validator.update_structure()

            # 使用SMC验证器进行多时间框架验证
            is_valid, message = self.smc_validator.final_check(current_price)

            if is_valid:
                self.stats["multi_tf_validations"] += 1
                self.stats["validation_passed"] += 1
            else:
                self.stats["validation_failed"] += 1

            return is_valid, message

        except Exception as e:
            logger.error(f"❌ 多时间框架验证失败: {e}")
            return False, f"多时间框架验证异常: {str(e)}"

    def _validate_with_orderflow(self, signal: dict, tick: dict) -> Tuple[bool, str]:
        """
        使用订单流验证信号

        参数:
            signal: Triple-A信号
            tick: 当前tick数据

        返回:
            tuple: (验证结果, 验证消息)
        """
        if not self.config.orderflow_validation_enabled or not self.orderflow_validator:
            return True, "订单流验证未启用"

        try:
            # 处理当前tick
            self.orderflow_validator.process_tick(tick)

            # 获取相关tick数据（使用时间窗口）
            relevant_ticks = self.tick_window[-100:]  # 最近100个tick

            # 获取适应性验证参数
            adaptive_params = self._get_adaptive_validation_params(tick)
            cvd_threshold = adaptive_params.get('cvd_threshold', self.config.orderflow_cvd_threshold)

            # 使用订单流验证器验证信号
            is_valid, message = self.orderflow_validator.validate_aggression_with_orderflow(
                signal, relevant_ticks, cvd_threshold=cvd_threshold
            )

            if is_valid:
                self.stats["orderflow_validations"] += 1
                self.stats["validation_passed"] += 1
            else:
                self.stats["validation_failed"] += 1

            return is_valid, message

        except Exception as e:
            logger.error(f"❌ 订单流验证失败: {e}")
            return False, f"订单流验证异常: {str(e)}"

    def _get_adaptive_validation_params(self, tick: dict) -> Dict[str, Any]:
        """
        获取适应性验证参数

        参数:
            tick: 当前tick数据

        返回:
            dict: 验证参数
        """
        if not self.config.adaptive_validation_enabled or not hasattr(self, 'market_environment_analyzer'):
            # 返回默认参数
            return {
                'cvd_threshold': self.config.orderflow_cvd_threshold,
                'min_value_area_strength': self.config.min_value_area_strength,
                'validation_mode': 'NORMAL'
            }

        try:
            # 获取K线数据（简化版本）
            # 在实际应用中，应该从context获取K线数据
            if hasattr(self.context, 'get_historical_data'):
                df = self.context.get_historical_data(timeframe='5m', limit=100)
            else:
                df = None

            # 分析市场环境
            environment = self.market_environment_analyzer.analyze_environment(df, [tick])

            # 获取适应性验证参数
            validation_params = self.market_environment_analyzer.get_validation_parameters(environment)

            # 记录环境信息
            logger.debug(f"🔧 适应性验证参数: 模式={validation_params.get('validation_mode', 'NORMAL')}, "
                        f"CVD阈值={validation_params.get('cvd_threshold', 0.7):.2f}, "
                        f"环境分数={environment.get('environment_score', 50.0):.1f}")

            return validation_params

        except Exception as e:
            logger.error(f"❌ 适应性验证参数获取失败: {e}")
            return {
                'cvd_threshold': self.config.orderflow_cvd_threshold,
                'min_value_area_strength': self.config.min_value_area_strength,
                'validation_mode': 'NORMAL'
            }

    async def _record_signal(self, signal: dict, tick: dict):
        """记录信号到上下文"""
        self.context.update_signal(signal)

        # 更新统计数据
        signal_type = signal.get('type', '')
        if 'ABSORPTION' in signal_type:
            self.stats["absorption_signals"] += 1
        elif 'ACCUMULATION' in signal_type:
            self.stats["accumulation_signals"] += 1
        elif 'AGGRESSION' in signal_type:
            self.stats["aggression_signals"] += 1

        logger.debug(f"📝 记录Triple-A信号: {signal_type}")

    def get_stats(self) -> Dict[str, Any]:
        """获取检测器统计信息"""
        # 计算验证成功率
        total_validations = self.stats.get("validation_passed", 0) + self.stats.get("validation_failed", 0)
        validation_success_rate = (self.stats.get("validation_passed", 0) / total_validations * 100) if total_validations > 0 else 0.0

        return {
            **self.stats,
            "current_state": self.state.current_state,
            "absorption_score": self.state.absorption_score,
            "accumulation_score": self.state.accumulation_score,
            "aggression_score": self.state.aggression_score,
            "window_size": len(self.tick_window),
            "validation_success_rate": f"{validation_success_rate:.1f}%",
            "value_area_analyzer_initialized": self.value_area_analyzer is not None,
            "orderflow_validator_initialized": hasattr(self, 'orderflow_validator') and self.orderflow_validator is not None,
            "smc_validator_initialized": self.smc_validator is not None
        }

    def reset(self):
        """重置检测器状态"""
        self.state.reset()
        self.tick_window.clear()
        logger.info("🔄 Triple-A检测器已重置")