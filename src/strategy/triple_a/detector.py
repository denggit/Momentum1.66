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
            "total_ticks": 0
        }

        # 时间窗口缓存
        self.tick_window = []
        self.max_window_size = 1000  # 最多存储1000个tick

        logger.info(f"🚀 Triple-A检测器初始化完成")

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
        avg_volume = np.mean([t.get('volume', 0) for t in self.tick_window[-100:] if t.get('volume', 0) > 0])
        if avg_volume <= 0:
            return None

        # 计算价格稳定性
        recent_prices = [t.get('price', 0.0) for t in self.tick_window[-30:] if t.get('price', 0.0) > 0]
        if len(recent_prices) < 10:
            return None

        price_range = (max(recent_prices) - min(recent_prices)) / min(recent_prices)
        price_stable = price_range < self.config.absorption_price_threshold * 2

        # 检测异常大单
        current_volume = tick.get('volume', 0)
        large_order = current_volume > avg_volume * self.config.absorption_volume_ratio

        # 计算买量/卖量比率（简化版）
        # 实际应用中需要从tick数据中提取买卖方向
        buy_sell_ratio = 1.0  # 默认值，实际需要计算

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
            self.state.accumulation_low = current_price
            self.state.accumulation_high = current_price
        else:
            self.state.accumulation_low = min(self.state.accumulation_low, current_price)
            self.state.accumulation_high = max(self.state.accumulation_high, current_price)

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
        current_volume = tick.get('volume', 0)

        # 计算平均成交量
        avg_volume = np.mean([t.get('volume', 0) for t in self.tick_window[-100:] if t.get('volume', 0) > 0])
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

            direction = "UP" if breakout_up else "DOWN" if breakout_down else "UNKNOWN"

            logger.warning(f"🚨 Aggression触发！得分: {aggression_score:.2f}, "
                          f"方向: {direction}, 价格: {current_price:.2f}")

            return {
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

        # 如果Aggression检测超时，返回IDLE状态
        time_since_accumulation = time.time() - self.state.accumulation_start_time
        if time_since_accumulation > self.config.failed_auction_window_seconds:
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
        avg_volume = np.mean([t.get('volume', 0) for t in self.tick_window[-100:] if t.get('volume', 0) > 0])
        current_volume = tick.get('volume', 0)
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
            score += 0.4

        # 大单吸收权重
        if large_order:
            score += 0.3

        # 买量/卖量比率权重（简化）
        if 1.5 < buy_sell_ratio < 3.0:
            score += 0.2

        # 时间持续性权重
        if tick_count > 50:
            score += 0.1

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

        volumes = [t.get('volume', 0) for t in self.tick_window[-20:]]

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
        """检查订单流方向是否与突破方向一致（简化版）"""
        # 实际应用中需要从tick数据中提取CVD等信息
        # 这里返回True作为占位符
        return True

    def _check_orderflow_reversal(self, tick: dict) -> bool:
        """检查订单流是否反转（简化版）"""
        # 实际应用中需要从tick数据中提取CVD方向变化
        # 这里返回True作为占位符
        return True

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
        return {
            **self.stats,
            "current_state": self.state.current_state,
            "absorption_score": self.state.absorption_score,
            "accumulation_score": self.state.accumulation_score,
            "aggression_score": self.state.aggression_score,
            "window_size": len(self.tick_window)
        }

    def reset(self):
        """重置检测器状态"""
        self.state.reset()
        self.tick_window.clear()
        logger.info("🔄 Triple-A检测器已重置")