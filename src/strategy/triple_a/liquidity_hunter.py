#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
专业流动性狩猎识别器
实现Fabio方法中的机构流动性狩猎模式检测
"""

import numpy as np
from typing import Dict, Any, Optional, List, Tuple, Deque
from collections import deque
import time
from enum import Enum

from src.utils.log import get_logger

logger = get_logger(__name__)


class LiquidityHuntType(Enum):
    """流动性狩猎类型"""
    STOP_LOSS_HUNT = "STOP_LOSS_HUNT"  # 止损单狩猎
    LIQUIDITY_POOL_HUNT = "LIQUIDITY_POOL_HUNT"  # 流动性池狩猎
    INSTITUTIONAL_HUNT = "INSTITUTIONAL_HUNT"  # 机构狩猎
    TRAP_HUNT = "TRAP_HUNT"  # 陷阱狩猎
    WASH_TRADE_HUNT = "WASH_TRADE_HUNT"  # 洗单狩猎


class LiquidityHuntPattern:
    """流动性狩猎模式"""

    def __init__(self, pattern_type: LiquidityHuntType, confidence: float, details: Dict[str, Any]):
        self.pattern_type = pattern_type
        self.confidence = confidence  # 置信度 0-1
        self.details = details
        self.timestamp = time.time()
        self.trigger_price = details.get('trigger_price', 0.0)
        self.volume = details.get('volume', 0.0)

    def __str__(self) -> str:
        return f"{self.pattern_type.value}({self.confidence:.1%}) @ {self.trigger_price:.2f}"


class LiquidityHunter:
    """专业流动性狩猎识别器

    实现Fabio方法中的机构流动性狩猎模式检测，包括：
    1. 止损单狩猎检测
    2. 流动性池狩猎识别
    3. 机构狩猎模式识别
    4. 陷阱模式检测
    5. 洗单模式检测
    """

    def __init__(self,
                 min_hunt_volume: float = 100000.0,
                 price_spike_threshold: float = 0.003,  # 0.3%
                 volume_spike_ratio: float = 5.0,
                 direction_consistency_threshold: float = 0.8,
                 reversal_confirmation_window: int = 10):
        """
        初始化流动性狩猎识别器

        参数:
            min_hunt_volume: 最小狩猎成交量 (USD)
            price_spike_threshold: 价格突刺阈值 (百分比)
            volume_spike_ratio: 成交量突刺比率
            direction_consistency_threshold: 方向一致性阈值
            reversal_confirmation_window: 反转确认窗口 (tick数)
        """
        self.min_hunt_volume = min_hunt_volume
        self.price_spike_threshold = price_spike_threshold
        self.volume_spike_ratio = volume_spike_ratio
        self.direction_consistency_threshold = direction_consistency_threshold
        self.reversal_confirmation_window = reversal_confirmation_window

        # 数据历史
        self.price_history: Deque[float] = deque(maxlen=1000)
        self.volume_history: Deque[float] = deque(maxlen=1000)
        self.side_history: Deque[str] = deque(maxlen=1000)  # 'buy' or 'sell'
        self.timestamp_history: Deque[float] = deque(maxlen=1000)

        # 检测到的模式
        self.detected_patterns: List[LiquidityHuntPattern] = []
        self.max_patterns = 50

        # 统计信息
        self.stats = {
            "total_ticks": 0,
            "patterns_detected": 0,
            "stop_loss_hunts": 0,
            "liquidity_pool_hunts": 0,
            "institutional_hunts": 0,
            "trap_hunts": 0,
            "wash_trades": 0,
            "false_positives": 0
        }

        # 缓存最近的分析结果
        self._last_analysis: Optional[Dict[str, Any]] = None
        self._last_analysis_time = 0.0
        self._analysis_cache_ttl = 5.0  # 5秒缓存

        logger.info(f"🚀 LiquidityHunter初始化完成: "
                   f"min_volume=${min_hunt_volume:,.0f}, "
                   f"price_spike={price_spike_threshold*100:.1f}%, "
                   f"vol_spike={volume_spike_ratio:.1f}x")

    def process_tick(self, tick: Dict[str, Any]) -> Optional[LiquidityHuntPattern]:
        """
        处理tick数据，检测流动性狩猎模式

        参数:
            tick: tick数据，包含price, size, side, ts等字段

        返回:
            LiquidityHuntPattern: 检测到的模式，如果没有则返回None
        """
        self.stats["total_ticks"] += 1

        # 提取数据
        price = tick.get('price', 0.0)
        size = tick.get('size', 0.0)
        side = tick.get('side', '')
        timestamp = tick.get('ts', time.time())

        # 更新历史数据
        self.price_history.append(price)
        self.volume_history.append(size)
        self.side_history.append(side)
        self.timestamp_history.append(timestamp)

        # 检查是否满足基本条件
        if len(self.price_history) < 20 or size <= 0:
            return None

        # 计算订单价值
        order_value = price * size
        if order_value < self.min_hunt_volume:
            return None

        # 1. 检测止损单狩猎
        stop_loss_pattern = self._detect_stop_loss_hunt(tick)
        if stop_loss_pattern:
            self._record_pattern(stop_loss_pattern)
            return stop_loss_pattern

        # 2. 检测流动性池狩猎
        liquidity_pool_pattern = self._detect_liquidity_pool_hunt(tick)
        if liquidity_pool_pattern:
            self._record_pattern(liquidity_pool_pattern)
            return liquidity_pool_pattern

        # 3. 检测机构狩猎
        institutional_pattern = self._detect_institutional_hunt(tick)
        if institutional_pattern:
            self._record_pattern(institutional_pattern)
            return institutional_pattern

        # 4. 检测陷阱模式
        trap_pattern = self._detect_trap_hunt(tick)
        if trap_pattern:
            self._record_pattern(trap_pattern)
            return trap_pattern

        # 5. 检测洗单模式
        wash_trade_pattern = self._detect_wash_trade(tick)
        if wash_trade_pattern:
            self._record_pattern(wash_trade_pattern)
            return wash_trade_pattern

        return None

    def _detect_stop_loss_hunt(self, tick: Dict[str, Any]) -> Optional[LiquidityHuntPattern]:
        """
        检测止损单狩猎模式

        特征:
        1. 大单迅速推动价格
        2. 价格突破关键水平
        3. 然后迅速反转
        4. 成交量集中在突破点
        """
        try:
            if len(self.price_history) < 30:
                return None

            current_price = tick.get('price', 0.0)
            current_size = tick.get('size', 0.0)
            current_side = tick.get('side', '')

            # 1. 检查是否有近期价格突刺
            recent_prices = list(self.price_history)[-20:]
            recent_volumes = list(self.volume_history)[-20:]
            recent_sides = list(self.side_history)[-20:]

            if len(recent_prices) < 10:
                return None

            # 计算价格变动
            price_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]

            # 检查方向一致性
            buy_count = sum(1 for s in recent_sides if s == 'buy')
            sell_count = sum(1 for s in recent_sides if s == 'sell')
            dominant_side = 'buy' if buy_count > sell_count else 'sell'

            # 检查成交量突刺
            avg_volume = np.mean(recent_volumes[:-5]) if len(recent_volumes) > 5 else 0.0
            current_volume_ratio = current_size / avg_volume if avg_volume > 0 else 0.0

            # 止损单狩猎特征
            if (abs(price_change) > self.price_spike_threshold and
                current_volume_ratio > self.volume_spike_ratio and
                (buy_count >= 16 or sell_count >= 16) and  # 高度方向一致性
                dominant_side == current_side):

                # 检查是否有反转迹象（等待后续tick确认）
                # 这里先标记为潜在止损单狩猎

                confidence = min(0.7, abs(price_change) / 0.01)  # 价格变动越大，置信度越高

                pattern = LiquidityHuntPattern(
                    pattern_type=LiquidityHuntType.STOP_LOSS_HUNT,
                    confidence=confidence,
                    details={
                        'trigger_price': current_price,
                        'price_change_pct': price_change * 100,
                        'volume': current_size,
                        'side': current_side,
                        'volume_ratio': current_volume_ratio,
                        'direction_consistency': max(buy_count, sell_count) / 20.0,
                        'phase': 'INITIAL_SPIKE'
                    }
                )

                logger.warning(f"⚠️ 检测到潜在止损单狩猎: {pattern}")
                return pattern

        except Exception as e:
            logger.error(f"❌ 止损单狩猎检测失败: {e}")

        return None

    def _detect_liquidity_pool_hunt(self, tick: Dict[str, Any]) -> Optional[LiquidityHuntPattern]:
        """
        检测流动性池狩猎模式

        特征:
        1. 在关键价格水平聚集大量挂单
        2. 大单一次性吃掉多个价位挂单
        3. 价格迅速穿越多个价位
        4. 成交量分布异常
        """
        try:
            current_price = tick.get('price', 0.0)
            current_size = tick.get('size', 0.0)

            # 计算订单价值
            order_value = current_price * current_size

            # 流动性池狩猎通常涉及非常大的订单
            if order_value < self.min_hunt_volume * 3:  # 需要更大的订单
                return None

            # 检查价格穿越幅度
            if len(self.price_history) >= 10:
                recent_prices = list(self.price_history)[-10:]
                price_range = max(recent_prices) - min(recent_prices)
                avg_price = np.mean(recent_prices)

                # 价格穿越幅度（相对于平均价格）
                price_cross_pct = price_range / avg_price if avg_price > 0 else 0.0

                if price_cross_pct > 0.002:  # 穿越超过0.2%
                    # 检查成交量分布
                    recent_volumes = list(self.volume_history)[-10:]
                    volume_concentration = current_size / sum(recent_volumes) if sum(recent_volumes) > 0 else 0.0

                    if volume_concentration > 0.5:  # 当前tick成交量占近期总量的50%以上
                        confidence = min(0.8, price_cross_pct / 0.005)

                        pattern = LiquidityHuntPattern(
                            pattern_type=LiquidityHuntType.LIQUIDITY_POOL_HUNT,
                            confidence=confidence,
                            details={
                                'trigger_price': current_price,
                                'order_value': order_value,
                                'price_cross_pct': price_cross_pct * 100,
                                'volume_concentration': volume_concentration,
                                'phase': 'LIQUIDITY_SWEEP'
                            }
                        )

                        logger.warning(f"⚠️ 检测到潜在流动性池狩猎: {pattern}")
                        return pattern

        except Exception as e:
            logger.error(f"❌ 流动性池狩猎检测失败: {e}")

        return None

    def _detect_institutional_hunt(self, tick: Dict[str, Any]) -> Optional[LiquidityHuntPattern]:
        """
        检测机构狩猎模式

        特征:
        1. 多个大单连续执行
        2. 策略性价格推动
        3. 隐藏真实意图
        4. 复杂的时间模式
        """
        try:
            current_price = tick.get('price', 0.0)
            current_size = tick.get('size', 0.0)
            current_side = tick.get('side', '')

            # 需要足够的历史数据
            if len(self.price_history) < 50:
                return None

            # 分析最近50个tick的模式
            recent_prices = list(self.price_history)[-50:]
            recent_volumes = list(self.volume_history)[-50:]
            recent_sides = list(self.side_history)[-50:]
            recent_timestamps = list(self.timestamp_history)[-50:]

            # 检查时间分布
            if len(recent_timestamps) >= 2:
                time_diffs = [recent_timestamps[i+1] - recent_timestamps[i]
                            for i in range(len(recent_timestamps)-1)]
                avg_time_diff = np.mean(time_diffs) if time_diffs else 1.0

                # 机构狩猎通常有特定的时间模式（如规律间隔）
                time_std = np.std(time_diffs) if len(time_diffs) > 1 else 0.0
                time_regularity = 1.0 - min(time_std / avg_time_diff, 1.0) if avg_time_diff > 0 else 0.0

                # 检查大单序列
                large_orders = [i for i, vol in enumerate(recent_volumes)
                              if vol > np.mean(recent_volumes) * 2]

                # 检查大单的方向一致性
                if len(large_orders) >= 3:
                    large_order_sides = [recent_sides[i] for i in large_orders]
                    side_consistency = (max(large_order_sides.count('buy'),
                                          large_order_sides.count('sell')) / len(large_orders))

                    # 检查价格趋势
                    price_trend = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]

                    if (side_consistency > 0.7 and
                        abs(price_trend) > 0.001 and
                        time_regularity > 0.6):

                        confidence = min(0.75, side_consistency * time_regularity)

                        pattern = LiquidityHuntPattern(
                            pattern_type=LiquidityHuntType.INSTITUTIONAL_HUNT,
                            confidence=confidence,
                            details={
                                'trigger_price': current_price,
                                'price_trend_pct': price_trend * 100,
                                'side_consistency': side_consistency,
                                'time_regularity': time_regularity,
                                'large_orders_count': len(large_orders),
                                'phase': 'STRATEGIC_ACCUMULATION'
                            }
                        )

                        logger.warning(f"⚠️ 检测到潜在机构狩猎: {pattern}")
                        return pattern

        except Exception as e:
            logger.error(f"❌ 机构狩猎检测失败: {e}")

        return None

    def _detect_trap_hunt(self, tick: Dict[str, Any]) -> Optional[LiquidityHuntPattern]:
        """
        检测陷阱模式

        特征:
        1. 假突破
        2. 迅速反转
        3. 诱多/诱空
        4. 成交量异常
        """
        try:
            current_price = tick.get('price', 0.0)

            # 需要足够的历史数据来检测反转
            if len(self.price_history) < 30:
                return None

            # 检查是否有近期突破
            recent_prices = list(self.price_history)[-20:]
            middle_prices = list(self.price_history)[-30:-10]

            if len(recent_prices) < 10 or len(middle_prices) < 10:
                return None

            # 计算突破幅度
            middle_avg = np.mean(middle_prices)
            recent_avg = np.mean(recent_prices)
            break_pct = (recent_avg - middle_avg) / middle_avg if middle_avg > 0 else 0.0

            # 检查是否开始反转
            latest_prices = list(self.price_history)[-5:]
            if len(latest_prices) >= 3:
                latest_trend = (latest_prices[-1] - latest_prices[0]) / latest_prices[0] if latest_prices[0] > 0 else 0.0

                # 陷阱特征：突破后迅速反转
                if (abs(break_pct) > 0.001 and  # 有显著突破
                    latest_trend * break_pct < 0 and  # 方向反转
                    abs(latest_trend) > 0.0005):  # 反转幅度足够

                    confidence = min(0.7, abs(break_pct) / 0.005)

                    pattern = LiquidityHuntPattern(
                        pattern_type=LiquidityHuntType.TRAP_HUNT,
                        confidence=confidence,
                        details={
                            'trigger_price': current_price,
                            'break_pct': break_pct * 100,
                            'reversal_pct': latest_trend * 100,
                            'trap_type': 'BULL_TRAP' if break_pct > 0 else 'BEAR_TRAP',
                            'phase': 'TRAP_CONFIRMATION'
                        }
                    )

                    logger.warning(f"⚠️ 检测到潜在陷阱模式: {pattern}")
                    return pattern

        except Exception as e:
            logger.error(f"❌ 陷阱模式检测失败: {e}")

        return None

    def _detect_wash_trade(self, tick: Dict[str, Any]) -> Optional[LiquidityHuntPattern]:
        """
        检测洗单模式

        特征:
        1. 相同价格附近快速买卖
        2. 成交量异常但价格不变
        3. 制造虚假成交量
        """
        try:
            current_price = tick.get('price', 0.0)
            current_size = tick.get('size', 0.0)

            # 需要足够的历史数据
            if len(self.price_history) < 20:
                return None

            # 检查价格稳定性
            recent_prices = list(self.price_history)[-10:]
            price_std = np.std(recent_prices) if len(recent_prices) > 1 else 0.0
            avg_price = np.mean(recent_prices) if recent_prices else 0.0
            price_stability = 1.0 - min(price_std / avg_price * 100, 1.0) if avg_price > 0 else 0.0

            # 检查成交量异常
            recent_volumes = list(self.volume_history)[-10:]
            volume_avg = np.mean(recent_volumes) if recent_volumes else 0.0
            volume_std = np.std(recent_volumes) if len(recent_volumes) > 1 else 0.0

            # 洗单特征：高成交量但价格稳定
            if (price_stability > 0.95 and  # 价格极其稳定
                volume_avg > 0 and
                current_size > volume_avg * 3 and  # 成交量突刺
                volume_std / volume_avg > 2.0):  # 成交量波动大

                # 检查买卖方向切换频率
                recent_sides = list(self.side_history)[-10:]
                side_changes = sum(1 for i in range(len(recent_sides)-1)
                                 if recent_sides[i] != recent_sides[i+1])
                side_switch_rate = side_changes / (len(recent_sides) - 1) if len(recent_sides) > 1 else 0.0

                if side_switch_rate > 0.5:  # 频繁切换方向
                    confidence = min(0.8, side_switch_rate * price_stability)

                    pattern = LiquidityHuntPattern(
                        pattern_type=LiquidityHuntType.WASH_TRADE_HUNT,
                        confidence=confidence,
                        details={
                            'trigger_price': current_price,
                            'price_stability': price_stability,
                            'volume_spike_ratio': current_size / volume_avg if volume_avg > 0 else 0.0,
                            'side_switch_rate': side_switch_rate,
                            'phase': 'WASH_TRADE_DETECTED'
                        }
                    )

                    logger.warning(f"⚠️ 检测到潜在洗单模式: {pattern}")
                    return pattern

        except Exception as e:
            logger.error(f"❌ 洗单模式检测失败: {e}")

        return None

    def _record_pattern(self, pattern: LiquidityHuntPattern):
        """记录检测到的模式"""
        self.detected_patterns.append(pattern)
        self.stats["patterns_detected"] += 1

        # 更新具体类型统计
        if pattern.pattern_type == LiquidityHuntType.STOP_LOSS_HUNT:
            self.stats["stop_loss_hunts"] += 1
        elif pattern.pattern_type == LiquidityHuntType.LIQUIDITY_POOL_HUNT:
            self.stats["liquidity_pool_hunts"] += 1
        elif pattern.pattern_type == LiquidityHuntType.INSTITUTIONAL_HUNT:
            self.stats["institutional_hunts"] += 1
        elif pattern.pattern_type == LiquidityHuntType.TRAP_HUNT:
            self.stats["trap_hunts"] += 1
        elif pattern.pattern_type == LiquidityHuntType.WASH_TRADE_HUNT:
            self.stats["wash_trades"] += 1

        # 限制列表大小
        if len(self.detected_patterns) > self.max_patterns:
            self.detected_patterns.pop(0)

    def analyze_market_context(self) -> Dict[str, Any]:
        """
        分析当前市场上下文中的流动性狩猎风险

        返回:
            dict: 市场流动性分析结果
        """
        current_time = time.time()

        # 检查缓存
        if (self._last_analysis and
            current_time - self._last_analysis_time < self._analysis_cache_ttl):
            return self._last_analysis

        try:
            if len(self.detected_patterns) == 0:
                result = {
                    'liquidity_risk': 'LOW',
                    'risk_score': 0.0,
                    'recent_patterns': 0,
                    'active_hunts': False,
                    'recommendation': 'NORMAL_TRADING'
                }
            else:
                # 获取最近10分钟内的模式
                recent_patterns = [
                    p for p in self.detected_patterns
                    if current_time - p.timestamp < 600  # 10分钟内
                ]

                if not recent_patterns:
                    result = {
                        'liquidity_risk': 'LOW',
                        'risk_score': 0.0,
                        'recent_patterns': 0,
                        'active_hunts': False,
                        'recommendation': 'NORMAL_TRADING'
                    }
                else:
                    # 计算风险分数
                    recent_confidences = [p.confidence for p in recent_patterns]
                    avg_confidence = np.mean(recent_confidences) if recent_confidences else 0.0

                    # 检查是否有高置信度模式
                    high_confidence_patterns = [p for p in recent_patterns if p.confidence > 0.7]

                    risk_score = min(avg_confidence * len(recent_patterns) / 5.0, 1.0)

                    # 确定风险等级
                    if risk_score < 0.3:
                        liquidity_risk = 'LOW'
                        recommendation = 'NORMAL_TRADING'
                    elif risk_score < 0.6:
                        liquidity_risk = 'MEDIUM'
                        recommendation = 'CAUTION_ADVISED'
                    else:
                        liquidity_risk = 'HIGH'
                        recommendation = 'REDUCE_EXPOSURE'

                    # 检查是否有活跃狩猎
                    active_hunts = len(high_confidence_patterns) > 0

                    result = {
                        'liquidity_risk': liquidity_risk,
                        'risk_score': float(risk_score),
                        'recent_patterns': len(recent_patterns),
                        'active_hunts': active_hunts,
                        'high_confidence_patterns': len(high_confidence_patterns),
                        'avg_confidence': float(avg_confidence),
                        'recommendation': recommendation,
                        'last_pattern_time': recent_patterns[-1].timestamp if recent_patterns else 0.0
                    }

            # 缓存结果
            self._last_analysis = result
            self._last_analysis_time = current_time

            return result

        except Exception as e:
            logger.error(f"❌ 市场上下文分析失败: {e}")
            return {
                'liquidity_risk': 'UNKNOWN',
                'risk_score': 0.0,
                'recent_patterns': 0,
                'active_hunts': False,
                'recommendation': 'ERROR'
            }

    def get_recent_patterns(self, time_window: float = 300.0) -> List[LiquidityHuntPattern]:
        """
        获取指定时间窗口内的模式

        参数:
            time_window: 时间窗口（秒）

        返回:
            list: 时间窗口内的流动性狩猎模式
        """
        current_time = time.time()
        return [
            p for p in self.detected_patterns
            if current_time - p.timestamp < time_window
        ]

    def clear_history(self):
        """清除历史数据"""
        self.price_history.clear()
        self.volume_history.clear()
        self.side_history.clear()
        self.timestamp_history.clear()
        self.detected_patterns.clear()
        logger.info("🧹 LiquidityHunter历史数据已清除")

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            **self.stats,
            'price_history_size': len(self.price_history),
            'volume_history_size': len(self.volume_history),
            'detected_patterns_count': len(self.detected_patterns),
            'active_patterns': len(self.get_recent_patterns(300))  # 最近5分钟
        }


# 简化版工厂函数
def create_liquidity_hunter(config: Dict[str, Any] = None) -> LiquidityHunter:
    """创建流动性狩猎识别器"""
    if config is None:
        config = {}

    return LiquidityHunter(
        min_hunt_volume=config.get('min_hunt_volume', 100000.0),
        price_spike_threshold=config.get('price_spike_threshold', 0.003),
        volume_spike_ratio=config.get('volume_spike_ratio', 5.0),
        direction_consistency_threshold=config.get('direction_consistency_threshold', 0.8),
        reversal_confirmation_window=config.get('reversal_confirmation_window', 10)
    )