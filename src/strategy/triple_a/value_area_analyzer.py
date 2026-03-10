#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fabio平衡区间价值区间分析器 (Value Area Analyzer)
实现Fabio Valentini的Triple-A模型中平衡区间Volume Profile计算
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple, List

from src.utils.volume_profile import CompositeVolumeProfile
from src.utils.log import get_logger

logger = get_logger(__name__)


class ValueAreaAnalyzer:
    """Fabio平衡区间价值区间分析器"""

    def __init__(self, bin_size: float = 0.5, balance_range_pct: float = 0.02):
        """
        初始化价值区间分析器

        参数:
            bin_size: 分箱大小，默认0.5
            balance_range_pct: 平衡区间百分比，默认±2%
        """
        self.bin_size = bin_size
        self.balance_range_pct = balance_range_pct
        self.volume_profile = CompositeVolumeProfile(bin_size=bin_size)

        # 缓存最近计算的价值区间
        self._cache = {}
        self._cache_max_size = 10
        self._cache_ttl = 300  # 5分钟缓存时间

        logger.info(f"🚀 ValueAreaAnalyzer初始化完成: bin_size={bin_size}, balance_range={balance_range_pct*100:.1f}%")

    def calculate_fabio_value_area(self, df: pd.DataFrame, current_price: float,
                                   balance_range_pct: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Fabio方法：在当前价格附近的平衡区间内计算Volume Profile

        参数:
            df: K线数据DataFrame，包含'high', 'low', 'close', 'volume'列
            current_price: 当前价格
            balance_range_pct: 可选的平衡区间覆盖范围，默认使用初始化值

        返回:
            dict: 包含VAH, VAL, POC等价值区间信息，或None
        """
        if df is None or df.empty:
            logger.warning("❌ [ValueArea] 计算失败: 数据为空")
            return None

        if current_price <= 0:
            logger.warning("❌ [ValueArea] 计算失败: 当前价格无效")
            return None

        # 使用指定的平衡区间或默认值
        balance_range = balance_range_pct or self.balance_range_pct

        # 生成缓存键
        cache_key = f"{current_price:.2f}_{balance_range:.4f}_{len(df)}"
        if cache_key in self._cache:
            cached_result = self._cache[cache_key]
            if time.time() - cached_result['timestamp'] < self._cache_ttl:
                logger.debug(f"🔄 [ValueArea] 使用缓存结果")
                return cached_result['result']

        try:
            # 1. 确定平衡区间
            balance_low = current_price * (1 - balance_range)
            balance_high = current_price * (1 + balance_range)

            logger.debug(f"🔍 [ValueArea] 平衡区间: [{balance_low:.2f}, {balance_high:.2f}], "
                         f"当前价格: {current_price:.2f}")

            # 2. 筛选平衡区间内的K线数据
            # 使用K线的close价作为判断标准（Fabio方法）
            balance_mask = (df['close'] >= balance_low) & (df['close'] <= balance_high)
            balance_df = df[balance_mask].copy()

            if balance_df.empty:
                logger.warning(f"⚠️ [ValueArea] 平衡区间内无K线数据: 区间[{balance_low:.2f}, {balance_high:.2f}]")
                return None

            logger.debug(f"📊 [ValueArea] 平衡区间内K线数量: {len(balance_df)}")

            # 3. 在平衡区间内计算标准价值区间
            value_area_result = self.volume_profile.calculate_standard_value_area(balance_df)

            if value_area_result is None:
                logger.warning("⚠️ [ValueArea] 标准价值区间计算失败")
                return None

            # 4. 增强结果信息
            enhanced_result = {
                **value_area_result,
                'balance_low': float(balance_low),
                'balance_high': float(balance_high),
                'balance_range_pct': float(balance_range),
                'current_price': float(current_price),
                'kline_count': len(balance_df),
                'timestamp': time.time()
            }

            # 5. 计算相对位置信息
            enhanced_result.update(
                self._calculate_relative_positions(current_price, enhanced_result)
            )

            # 6. 缓存结果
            self._cache[cache_key] = {
                'result': enhanced_result,
                'timestamp': time.time()
            }

            # 清理过期缓存
            self._clean_cache()

            logger.info(f"✅ [ValueArea] 价值区间计算完成: "
                        f"VAH={enhanced_result['vah']:.2f}, "
                        f"VAL={enhanced_result['val']:.2f}, "
                        f"POC={enhanced_result['poc']:.2f}, "
                        f"位置={enhanced_result['position_relative_to_value_area']}")

            return enhanced_result

        except Exception as e:
            logger.error(f"❌ [ValueArea] Fabio价值区间计算失败: {e}")
            return None

    def _calculate_relative_positions(self, current_price: float, value_area: Dict[str, Any]) -> Dict[str, Any]:
        """计算当前价格相对于价值区间的位置"""
        vah = value_area['vah']
        val = value_area['val']
        poc = value_area['poc']

        # 计算相对位置
        if current_price > vah:
            position = "ABOVE_VAH"
            distance_to_vah = (current_price - vah) / vah
            distance_to_val = (current_price - val) / val
        elif current_price < val:
            position = "BELOW_VAL"
            distance_to_vah = (vah - current_price) / current_price
            distance_to_val = (val - current_price) / current_price
        elif current_price >= poc:
            position = "IN_VA_UPPER"
            distance_to_vah = (vah - current_price) / current_price
            distance_to_poc = (current_price - poc) / poc
        else:
            position = "IN_VA_LOWER"
            distance_to_val = (current_price - val) / current_price
            distance_to_poc = (poc - current_price) / current_price

        # 计算价值区间宽度
        va_width = (vah - val) / poc if poc > 0 else 0

        return {
            'position_relative_to_value_area': position,
            'distance_to_vah': float(distance_to_vah) if 'distance_to_vah' in locals() else 0.0,
            'distance_to_val': float(distance_to_val) if 'distance_to_val' in locals() else 0.0,
            'distance_to_poc': float(distance_to_poc) if 'distance_to_poc' in locals() else 0.0,
            'value_area_width_pct': float(va_width * 100),
            'is_in_value_area': val <= current_price <= vah,
            'is_near_poc': abs(current_price - poc) / poc < 0.001 if poc > 0 else False
        }

    def validate_aggression_with_value_area(self, signal: Dict[str, Any],
                                           value_area_result: Dict[str, Any]) -> Tuple[bool, str]:
        """
        使用价值区间验证Aggression信号

        参数:
            signal: Triple-A信号，包含'direction', 'price'等信息
            value_area_result: 价值区间计算结果

        返回:
            tuple: (是否通过验证, 验证消息)
        """
        if not signal or not value_area_result:
            return False, "信号或价值区间数据无效"

        direction = signal.get('direction', '').upper()
        price = signal.get('price', 0.0)
        breakout_price = signal.get('breakout_price', price)

        if direction not in ['UP', 'DOWN']:
            return False, f"无效的方向: {direction}"

        vah = value_area_result.get('vah', 0.0)
        val = value_area_result.get('val', 0.0)
        poc = value_area_result.get('poc', 0.0)

        if vah <= 0 or val <= 0:
            return False, "价值区间数据无效"

        # Fabio验证规则：
        # 1. 向上突破：价格应高于VAH，或至少远离VAL
        # 2. 向下突破：价格应低于VAL，或至少远离VAH

        if direction == 'UP':
            # 检查是否突破VAH或至少远离VAL
            if breakout_price > vah:
                return True, f"✅ 向上突破VAH ({breakout_price:.2f} > {vah:.2f})"
            elif breakout_price > poc:
                distance_to_vah = (vah - breakout_price) / breakout_price
                if distance_to_vah < 0.005:  # 距离VAH小于0.5%
                    return True, f"✅ 接近VAH向上突破 (距离{distance_to_vah*100:.2f}%)"
                else:
                    return False, f"⚠️ 向上突破但未突破VAH (距离{distance_to_vah*100:.2f}%)"
            else:
                return False, f"❌ 向上突破但价格低于POC ({breakout_price:.2f} < {poc:.2f})"

        else:  # direction == 'DOWN'
            # 检查是否突破VAL或至少远离VAH
            if breakout_price < val:
                return True, f"✅ 向下突破VAL ({breakout_price:.2f} < {val:.2f})"
            elif breakout_price < poc:
                distance_to_val = (breakout_price - val) / val
                if distance_to_val < 0.005:  # 距离VAL小于0.5%
                    return True, f"✅ 接近VAL向下突破 (距离{distance_to_val*100:.2f}%)"
                else:
                    return False, f"⚠️ 向下突破但未突破VAL (距离{distance_to_val*100:.2f}%)"
            else:
                return False, f"❌ 向下突破但价格高于POC ({breakout_price:.2f} > {poc:.2f})"

    def analyze_value_area_strength(self, value_area_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        分析价值区间的强度

        参数:
            value_area_result: 价值区间计算结果

        返回:
            dict: 强度分析结果
        """
        if not value_area_result:
            return {'strength': 'UNKNOWN', 'score': 0.0}

        try:
            total_volume = value_area_result.get('total_volume', 0.0)
            value_area_volume = value_area_result.get('value_area_volume', 0.0)
            value_area_ratio = value_area_result.get('value_area_ratio', 0.0)
            value_area_width_pct = value_area_result.get('value_area_width_pct', 0.0)

            # 计算强度分数 (0-100)
            score = 0.0

            # 1. 成交量集中度得分 (0-40分)
            if value_area_ratio >= 0.7:
                score += 40
            elif value_area_ratio >= 0.6:
                score += 30
            elif value_area_ratio >= 0.5:
                score += 20
            else:
                score += 10

            # 2. 价值区间宽度得分 (0-30分) - 越窄越强
            if value_area_width_pct <= 1.0:
                score += 30
            elif value_area_width_pct <= 2.0:
                score += 20
            elif value_area_width_pct <= 3.0:
                score += 10

            # 3. 绝对成交量得分 (0-30分)
            if total_volume > 0:
                # 这里需要根据具体品种调整阈值
                volume_level = min(total_volume / 1_000_000, 1.0)  # 假设100万为满分
                score += volume_level * 30

            # 确定强度等级
            if score >= 80:
                strength = 'STRONG'
            elif score >= 60:
                strength = 'MEDIUM'
            elif score >= 40:
                strength = 'WEAK'
            else:
                strength = 'VERY_WEAK'

            return {
                'strength': strength,
                'score': float(score),
                'value_area_ratio': float(value_area_ratio),
                'value_area_width_pct': float(value_area_width_pct),
                'total_volume': float(total_volume)
            }

        except Exception as e:
            logger.error(f"❌ [ValueArea] 强度分析失败: {e}")
            return {'strength': 'ERROR', 'score': 0.0}

    def get_validation_parameters(self, market_environment: str = 'NORMAL') -> Dict[str, Any]:
        """
        根据市场环境获取验证参数

        参数:
            market_environment: 市场环境 ('HIGH_VOLATILITY', 'NORMAL', 'LOW_VOLATILITY')

        返回:
            dict: 验证参数
        """
        # 基础参数
        params = {
            'balance_range_pct': self.balance_range_pct,
            'value_area_ratio': 0.7,
            'min_kline_count': 10,
            'max_value_area_width_pct': 3.0
        }

        # 根据市场环境调整
        if market_environment == 'HIGH_VOLATILITY':
            params.update({
                'balance_range_pct': self.balance_range_pct * 1.5,  # 扩大平衡区间
                'max_value_area_width_pct': 4.0,  # 放宽宽度限制
                'min_kline_count': 5  # 减少K线数量要求
            })
        elif market_environment == 'LOW_VOLATILITY':
            params.update({
                'balance_range_pct': self.balance_range_pct * 0.7,  # 缩小平衡区间
                'max_value_area_width_pct': 2.0,  # 收紧宽度限制
                'min_kline_count': 15  # 增加K线数量要求
            })

        return params

    def _clean_cache(self):
        """清理过期缓存"""
        current_time = time.time()
        keys_to_remove = []

        for key, cached in self._cache.items():
            if current_time - cached['timestamp'] > self._cache_ttl:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._cache[key]

        # 限制缓存大小
        if len(self._cache) > self._cache_max_size:
            # 删除最旧的缓存
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k]['timestamp'])
            del self._cache[oldest_key]

    def clear_cache(self):
        """清除所有缓存"""
        self._cache.clear()
        logger.debug("🧹 [ValueArea] 缓存已清除")


# 全局导入time模块
import time