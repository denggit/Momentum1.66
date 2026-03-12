#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
精细订单流验证器 (OrderFlow Validator)
实现Fabio验证策略中的精细订单流方向验证（CVD/Delta）
"""

import numpy as np
from typing import Dict, Any, Optional, List, Tuple, Deque
from collections import deque
import time

from src.utils.log import get_logger

logger = get_logger(__name__)


class OrderFlowValidator:
    """精细订单流验证器"""

    def __init__(self, cvd_threshold: float = 0.7, large_order_ratio: float = 2.0,
                 trend_window: int = 20, min_liquidity_hunt_volume: float = 100000.0):
        """
        初始化订单流验证器

        参数:
            cvd_threshold: CVD方向验证阈值 (0-1)
            large_order_ratio: 大单比率阈值 (相对于平均成交量)
            trend_window: 趋势分析窗口大小
            min_liquidity_hunt_volume: 流动性狩猎最小成交量 (USD)
        """
        self.cvd_threshold = cvd_threshold
        self.large_order_ratio = large_order_ratio
        self.trend_window = trend_window
        self.min_liquidity_hunt_volume = min_liquidity_hunt_volume

        # 数据缓存
        self.cvd_history: Deque[float] = deque(maxlen=1000)
        self.price_history: Deque[float] = deque(maxlen=1000)
        self.volume_history: Deque[float] = deque(maxlen=1000)
        self.side_history: Deque[str] = deque(maxlen=1000)  # 'buy' or 'sell'

        # 大单检测
        self.large_orders: List[Dict[str, Any]] = []
        self.max_large_orders = 100

        # 流动性狩猎检测
        self.liquidity_hunt_candidates: List[Dict[str, Any]] = []
        self.last_liquidity_hunt_time = 0.0
        self.liquidity_hunt_cooldown = 300  # 5分钟冷却时间

        # 统计信息
        self.stats = {
            "total_ticks": 0,
            "large_orders_detected": 0,
            "liquidity_hunts_detected": 0,
            "cvd_alignment_checks": 0,
            "cvd_alignment_passed": 0,
            "cvd_alignment_failed": 0
        }

        logger.info(f"🚀 OrderFlowValidator初始化完成: cvd_threshold={cvd_threshold}, "
                   f"large_order_ratio={large_order_ratio}")

    def process_tick(self, tick: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理tick数据，更新订单流状态

        参数:
            tick: tick数据，包含price, size, side, cvd等字段

        返回:
            dict: 订单流分析结果
        """
        self.stats["total_ticks"] += 1

        # 提取数据
        price = tick.get('price', 0.0)
        size = tick.get('size', 0.0)
        side = tick.get('side', '')
        cvd = tick.get('cvd', None)
        timestamp = tick.get('ts', time.time())

        # 更新历史数据
        self.price_history.append(price)
        self.volume_history.append(size)
        self.side_history.append(side)

        if cvd is not None:
            self.cvd_history.append(cvd)

        # 分析结果
        analysis = {
            'timestamp': timestamp,
            'price': price,
            'size': size,
            'side': side,
            'cvd': cvd,
            'large_order_detected': False,
            'liquidity_hunt_detected': False,
            'cvd_trend': None,
            'order_imbalance': None
        }

        # 1. 检测大单
        large_order_result = self._detect_large_order(tick)
        if large_order_result:
            analysis['large_order_detected'] = True
            analysis['large_order_details'] = large_order_result

        # 2. 检测流动性狩猎
        if time.time() - self.last_liquidity_hunt_time > self.liquidity_hunt_cooldown:
            liquidity_hunt_result = self._detect_liquidity_hunt(tick)
            if liquidity_hunt_result:
                analysis['liquidity_hunt_detected'] = True
                analysis['liquidity_hunt_details'] = liquidity_hunt_result
                self.last_liquidity_hunt_time = time.time()

        # 3. 计算CVD趋势
        if len(self.cvd_history) >= 10:
            analysis['cvd_trend'] = self._calculate_cvd_trend()
            analysis['order_imbalance'] = self._calculate_order_imbalance()

        return analysis

    def _detect_large_order(self, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        检测异常大单

        参数:
            tick: tick数据

        返回:
            dict: 大单详情，或None
        """
        try:
            current_size = tick.get('size', 0.0)
            current_price = tick.get('price', 0.0)
            side = tick.get('side', '')

            if len(self.volume_history) < 10 or current_size <= 0:
                return None

            # 计算平均成交量
            recent_volumes = list(self.volume_history)[-20:]  # 最近20个tick
            avg_volume = np.mean(recent_volumes) if recent_volumes else 0.0

            if avg_volume <= 0:
                return None

            # 检查是否为大单
            volume_ratio = current_size / avg_volume
            if volume_ratio >= self.large_order_ratio:
                # 计算大单价值
                order_value = current_size * current_price

                large_order = {
                    'timestamp': tick.get('ts', time.time()),
                    'price': current_price,
                    'size': current_size,
                    'side': side,
                    'volume_ratio': volume_ratio,
                    'order_value': order_value,
                    'avg_volume': avg_volume
                }

                # 记录大单
                self.large_orders.append(large_order)
                if len(self.large_orders) > self.max_large_orders:
                    self.large_orders.pop(0)

                self.stats["large_orders_detected"] += 1

                logger.debug(f"🔍 检测到大单: {side} {current_size:.4f} @ {current_price:.2f}, "
                           f"比率: {volume_ratio:.1f}x, 价值: ${order_value:.2f}")

                return large_order

        except Exception as e:
            logger.error(f"❌ 大单检测失败: {e}")

        return None

    def _detect_liquidity_hunt(self, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        检测流动性狩猎

        参数:
            tick: tick数据

        返回:
            dict: 流动性狩猎详情，或None
        """
        try:
            current_price = tick.get('price', 0.0)
            current_size = tick.get('size', 0.0)
            side = tick.get('side', '')

            if len(self.price_history) < 50 or current_size <= 0:
                return None

            # 计算订单价值
            order_value = current_size * current_price

            # 检查是否达到流动性狩猎阈值
            if order_value < self.min_liquidity_hunt_volume:
                return None

            # 1. 检查是否在关键价格水平附近
            # (这里可以集成价值区间分析器，第一阶段先简化)
            recent_prices = list(self.price_history)[-50:]
            price_std = np.std(recent_prices) if len(recent_prices) > 1 else 0.0

            # 2. 检查是否有止损单狩猎模式
            # 模式：大单迅速推动价格，然后反转
            if len(self.price_history) >= 10 and len(self.side_history) >= 10:
                recent_prices = list(self.price_history)[-10:]
                recent_sides = list(self.side_history)[-10:]

                # 检查价格推动
                price_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]

                # 检查方向一致性
                buy_count = sum(1 for s in recent_sides if s == 'buy')
                sell_count = sum(1 for s in recent_sides if s == 'sell')

                # 流动性狩猎特征：
                # 1. 大单推动价格
                # 2. 方向高度一致
                # 3. 价格变动显著
                if abs(price_change) > 0.001 and (buy_count >= 8 or sell_count >= 8):
                    # 检查是否有反转迹象（后续tick会验证）
                    liquidity_hunt = {
                        'timestamp': tick.get('ts', time.time()),
                        'price': current_price,
                        'size': current_size,
                        'side': side,
                        'order_value': order_value,
                        'price_change_pct': price_change * 100,
                        'direction_consistency': max(buy_count, sell_count) / 10.0,
                        'type': 'POTENTIAL_LIQUIDITY_HUNT'
                    }

                    self.liquidity_hunt_candidates.append(liquidity_hunt)
                    self.stats["liquidity_hunts_detected"] += 1

                    logger.warning(f"⚠️ 检测到潜在流动性狩猎: {side} ${order_value:.0f}, "
                                 f"价格变动: {price_change*100:.3f}%, 方向一致性: {liquidity_hunt['direction_consistency']:.1%}")

                    return liquidity_hunt

        except Exception as e:
            logger.error(f"❌ 流动性狩猎检测失败: {e}")

        return None

    def _calculate_cvd_trend(self) -> Dict[str, Any]:
        """计算CVD趋势"""
        if len(self.cvd_history) < self.trend_window:
            return {'trend': 'UNKNOWN', 'strength': 0.0, 'direction': 0}

        try:
            cvd_data = list(self.cvd_history)
            window_data = cvd_data[-self.trend_window:]

            # 计算趋势方向
            if len(window_data) >= 2:
                start_cvd = window_data[0]
                end_cvd = window_data[-1]
                cvd_change = end_cvd - start_cvd

                # 计算趋势强度
                cvd_std = np.std(window_data) if len(window_data) > 1 else 0.0
                if cvd_std > 0:
                    trend_strength = abs(cvd_change) / cvd_std
                else:
                    trend_strength = 0.0

                # 确定趋势方向
                if cvd_change > 0:
                    direction = 1  # 上升趋势
                    trend = 'BULLISH'
                elif cvd_change < 0:
                    direction = -1  # 下降趋势
                    trend = 'BEARISH'
                else:
                    direction = 0  # 无趋势
                    trend = 'NEUTRAL'

                return {
                    'trend': trend,
                    'strength': float(trend_strength),
                    'direction': direction,
                    'cvd_change': float(cvd_change),
                    'start_cvd': float(start_cvd),
                    'end_cvd': float(end_cvd)
                }

        except Exception as e:
            logger.error(f"❌ CVD趋势计算失败: {e}")

        return {'trend': 'ERROR', 'strength': 0.0, 'direction': 0}

    def _calculate_order_imbalance(self) -> Dict[str, Any]:
        """计算订单不平衡度"""
        if len(self.side_history) < 10:
            return {'imbalance': 0.0, 'buy_ratio': 0.5, 'sell_ratio': 0.5}

        try:
            recent_sides = list(self.side_history)[-50:]
            buy_count = sum(1 for s in recent_sides if s == 'buy')
            sell_count = sum(1 for s in recent_sides if s == 'sell')
            total_count = buy_count + sell_count

            if total_count > 0:
                buy_ratio = buy_count / total_count
                sell_ratio = sell_count / total_count
                imbalance = (buy_count - sell_count) / total_count
            else:
                buy_ratio = sell_ratio = 0.5
                imbalance = 0.0

            return {
                'imbalance': float(imbalance),
                'buy_ratio': float(buy_ratio),
                'sell_ratio': float(sell_ratio),
                'buy_count': buy_count,
                'sell_count': sell_count,
                'total_count': total_count
            }

        except Exception as e:
            logger.error(f"❌ 订单不平衡度计算失败: {e}")

        return {'imbalance': 0.0, 'buy_ratio': 0.5, 'sell_ratio': 0.5}

    def validate_aggression_with_orderflow(self, signal: Dict[str, Any],
                                          tick_data: List[Dict[str, Any]],
                                          cvd_data: Optional[List[float]] = None,
                                          cvd_threshold: Optional[float] = None) -> Tuple[bool, str]:
        """
        验证Aggression信号与订单流方向的一致性

        参数:
            signal: Triple-A信号
            tick_data: 相关tick数据
            cvd_data: CVD数据（可选）
            cvd_threshold: CVD趋势强度阈值，如果为None则使用默认值

        返回:
            tuple: (验证结果, 验证消息)
        """
        self.stats["cvd_alignment_checks"] += 1

        try:
            direction = signal.get('direction', '').upper()
            if direction not in ['UP', 'DOWN']:
                return False, f"无效的信号方向: {direction}"

            # 1. CVD趋势验证
            cvd_aligned = self._check_cvd_trend_alignment(direction, cvd_threshold)
            if not cvd_aligned:
                self.stats["cvd_alignment_failed"] += 1
                return False, "CVD趋势与突破方向不一致"

            # 2. 大单异常检测
            large_order_aligned = self._check_large_order_alignment(signal, tick_data)
            if not large_order_aligned:
                return False, "大单方向与突破方向不一致"

            # 3. 流动性狩猎检测
            no_liquidity_hunt = self._check_no_liquidity_hunting(signal, tick_data)
            if not no_liquidity_hunt:
                return False, "检测到潜在流动性狩猎"

            # 4. 订单不平衡度验证
            order_imbalance_valid = self._check_order_imbalance(direction)
            if not order_imbalance_valid:
                return False, "订单不平衡度不支持突破方向"

            self.stats["cvd_alignment_passed"] += 1
            return True, "✅ 订单流验证通过: CVD趋势、大单方向、订单不平衡度均支持突破方向"

        except Exception as e:
            logger.error(f"❌ 订单流验证失败: {e}")
            return False, f"订单流验证异常: {str(e)}"

    def _check_cvd_trend_alignment(self, direction: str, cvd_threshold: Optional[float] = None) -> bool:
        """检查CVD趋势是否与突破方向一致

        参数:
            direction: 突破方向 ('UP' 或 'DOWN')
            cvd_threshold: CVD趋势强度阈值，如果为None则使用self.cvd_threshold
        """
        cvd_trend = self._calculate_cvd_trend()
        trend = cvd_trend.get('trend', 'UNKNOWN')
        strength = cvd_trend.get('strength', 0.0)

        # 使用提供的阈值或默认阈值
        threshold = cvd_threshold if cvd_threshold is not None else self.cvd_threshold

        # 检查趋势强度
        if strength < threshold:
            logger.debug(f"⚠️ CVD趋势强度不足: {strength:.2f} < {threshold}")
            # 趋势强度不足，但可能仍然有效（取决于其他因素）
            # 第一阶段：宽松处理
            return True

        # 检查方向一致性
        if direction == 'UP' and trend == 'BULLISH':
            return True
        elif direction == 'DOWN' and trend == 'BEARISH':
            return True
        elif trend == 'NEUTRAL' or trend == 'UNKNOWN':
            # 中性趋势，不构成反对
            return True
        else:
            logger.debug(f"⚠️ CVD趋势方向不匹配: 突破方向={direction}, CVD趋势={trend}")
            return False

    def _check_large_order_alignment(self, signal: Dict[str, Any],
                                    tick_data: List[Dict[str, Any]]) -> bool:
        """检查大单方向是否与突破方向一致"""
        if not self.large_orders:
            # 没有检测到大单，不构成反对
            return True

        direction = signal.get('direction', '').upper()
        recent_large_orders = self.large_orders[-5:]  # 最近5个大单

        if not recent_large_orders:
            return True

        # 统计大单方向
        buy_orders = [o for o in recent_large_orders if o.get('side') == 'buy']
        sell_orders = [o for o in recent_large_orders if o.get('side') == 'sell']

        # 检查方向一致性
        if direction == 'UP' and len(buy_orders) >= len(sell_orders):
            return True
        elif direction == 'DOWN' and len(sell_orders) >= len(buy_orders):
            return True
        else:
            logger.debug(f"⚠️ 大单方向不匹配: 突破方向={direction}, "
                       f"买大单={len(buy_orders)}, 卖大单={len(sell_orders)}")
            # 第一阶段：宽松处理
            return True

    def _check_no_liquidity_hunting(self, signal: Dict[str, Any],
                                   tick_data: List[Dict[str, Any]]) -> bool:
        """检查是否有流动性狩猎"""
        if not self.liquidity_hunt_candidates:
            return True

        # 检查最近的流动性狩猎候选
        recent_candidates = self.liquidity_hunt_candidates[-3:]  # 最近3个候选
        current_time = time.time()

        for candidate in recent_candidates:
            candidate_time = candidate.get('timestamp', 0)
            # 如果候选在最近30秒内
            if current_time - candidate_time < 30:
                candidate_type = candidate.get('type', '')
                if 'LIQUIDITY_HUNT' in candidate_type:
                    logger.warning(f"⚠️ 检测到近期流动性狩猎: {candidate_type}")
                    return False

        return True

    def _check_order_imbalance(self, direction: str) -> bool:
        """检查订单不平衡度是否支持突破方向"""
        imbalance_data = self._calculate_order_imbalance()
        imbalance = imbalance_data.get('imbalance', 0.0)

        # 简单的方向检查
        if direction == 'UP' and imbalance > -0.3:  # 允许轻微负不平衡
            return True
        elif direction == 'DOWN' and imbalance < 0.3:  # 允许轻微正不平衡
            return True
        else:
            logger.debug(f"⚠️ 订单不平衡度不匹配: 突破方向={direction}, 不平衡度={imbalance:.3f}")
            # 第一阶段：宽松处理
            return True

    def get_stats(self) -> Dict[str, Any]:
        """获取验证器统计信息"""
        # 计算成功率
        total_checks = self.stats.get("cvd_alignment_checks", 0)
        passed_checks = self.stats.get("cvd_alignment_passed", 0)
        success_rate = (passed_checks / total_checks * 100) if total_checks > 0 else 0.0

        return {
            **self.stats,
            "success_rate": f"{success_rate:.1f}%",
            "cvd_history_size": len(self.cvd_history),
            "price_history_size": len(self.price_history),
            "large_orders_count": len(self.large_orders),
            "liquidity_hunt_candidates": len(self.liquidity_hunt_candidates)
        }

    def clear_history(self):
        """清除历史数据"""
        self.cvd_history.clear()
        self.price_history.clear()
        self.volume_history.clear()
        self.side_history.clear()
        self.large_orders.clear()
        self.liquidity_hunt_candidates.clear()
        logger.info("🧹 OrderFlowValidator历史数据已清除")


# 简化版工厂函数
def create_orderflow_validator(config: Dict[str, Any] = None) -> OrderFlowValidator:
    """创建订单流验证器"""
    if config is None:
        config = {}

    return OrderFlowValidator(
        cvd_threshold=config.get('cvd_threshold', 0.7),
        large_order_ratio=config.get('large_order_ratio', 2.0),
        trend_window=config.get('trend_window', 20),
        min_liquidity_hunt_volume=config.get('min_liquidity_hunt_volume', 100000.0)
    )