#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
市场环境分析器 (Market Environment Analyzer)
实现Fabio验证策略中的市场环境适应性判断
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, List, Tuple
import time
from enum import Enum

from src.utils.log import get_logger

logger = get_logger(__name__)


class VolatilityState(Enum):
    """波动率状态"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TrendState(Enum):
    """趋势状态"""
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGING = "RANGING"


class LiquidityState(Enum):
    """流动性状态"""
    THIN = "THIN"  # 稀薄
    NORMAL = "NORMAL"  # 正常
    LIQUID = "LIQUID"  # 充裕


class TradingSession(Enum):
    """交易时段"""
    ASIA = "ASIA"  # 亚洲时段 (00:00-08:00 UTC)
    EUROPE = "EUROPE"  # 欧洲时段 (08:00-16:00 UTC)
    US = "US"  # 美国时段 (16:00-24:00 UTC)
    OVERLAP = "OVERLAP"  # 重叠时段


class MarketEnvironmentAnalyzer:
    """市场环境分析器"""

    def __init__(self,
                 volatility_threshold_low: float = 0.5,
                 volatility_threshold_high: float = 2.0,
                 trend_lookback_periods: int = 20,
                 atr_period: int = 14):
        """
        初始化市场环境分析器

        参数:
            volatility_threshold_low: 低波动率阈值 (ATR倍数)
            volatility_threshold_high: 高波动率阈值 (ATR倍数)
            trend_lookback_periods: 趋势回看周期
            atr_period: ATR计算周期
        """
        self.volatility_threshold_low = volatility_threshold_low
        self.volatility_threshold_high = volatility_threshold_high
        self.trend_lookback_periods = trend_lookback_periods
        self.atr_period = atr_period

        # 状态缓存
        self._last_analysis = None
        self._last_analysis_time = 0
        self._cache_ttl = 60  # 缓存60秒

        # 历史状态记录
        self.history: List[Dict[str, Any]] = []
        self.max_history_size = 1000

        logger.info(f"🚀 MarketEnvironmentAnalyzer初始化完成: "
                   f"volatility_thresholds=[{volatility_threshold_low}, {volatility_threshold_high}], "
                   f"trend_lookback={trend_lookback_periods}")

    def analyze_environment(self,
                           df: pd.DataFrame,
                           tick_data: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        分析当前市场环境

        参数:
            df: K线数据DataFrame (至少包含'high', 'low', 'close', 'volume')
            tick_data: tick数据列表 (可选)

        返回:
            dict: 市场环境分析结果
        """
        # 检查缓存
        current_time = time.time()
        if (self._last_analysis and
                current_time - self._last_analysis_time < self._cache_ttl):
            return self._last_analysis

        try:
            if df is None or df.empty:
                return self._get_default_environment()

            # 1. 计算波动率状态
            volatility_state, atr_value, atr_pct = self._classify_volatility(df)

            # 2. 计算趋势状态
            trend_state, trend_strength, trend_direction = self._classify_trend(df)

            # 3. 评估流动性状态
            liquidity_state, volume_metrics = self._assess_liquidity(df, tick_data)

            # 4. 确定交易时段
            trading_session = self._get_trading_session()

            # 5. 计算整体市场状态
            market_state = self._determine_market_state(
                volatility_state, trend_state, liquidity_state, trading_session
            )

            # 6. 计算环境分数 (0-100)
            environment_score = self._calculate_environment_score(
                volatility_state, trend_state, liquidity_state, market_state
            )

            # 构建结果
            result = {
                'volatility_state': volatility_state.value,
                'volatility_details': {
                    'atr_value': float(atr_value),
                    'atr_pct': float(atr_pct),
                    'state': volatility_state.value
                },
                'trend_state': trend_state.value,
                'trend_details': {
                    'strength': float(trend_strength),
                    'direction': float(trend_direction),
                    'state': trend_state.value
                },
                'liquidity_state': liquidity_state.value,
                'liquidity_details': volume_metrics,
                'trading_session': trading_session.value,
                'market_state': market_state,
                'environment_score': float(environment_score),
                'timestamp': current_time,
                'is_high_volatility': volatility_state == VolatilityState.HIGH,
                'is_low_volatility': volatility_state == VolatilityState.LOW,
                'is_uptrend': trend_state == TrendState.UPTREND,
                'is_downtrend': trend_state == TrendState.DOWNTREND,
                'is_ranging': trend_state == TrendState.RANGING
            }

            # 缓存结果
            self._last_analysis = result
            self._last_analysis_time = current_time

            # 记录历史
            self.history.append(result.copy())
            if len(self.history) > self.max_history_size:
                self.history.pop(0)

            logger.debug(f"🔍 [MarketEnvironment] 分析完成: "
                        f"波动率={volatility_state.value}, "
                        f"趋势={trend_state.value}, "
                        f"流动性={liquidity_state.value}, "
                        f"分数={environment_score:.1f}")

            return result

        except Exception as e:
            logger.error(f"❌ 市场环境分析失败: {e}")
            return self._get_default_environment()

    def _classify_volatility(self, df: pd.DataFrame) -> Tuple[VolatilityState, float, float]:
        """分类波动率状态"""
        try:
            # 计算ATR
            if len(df) < self.atr_period + 1:
                return VolatilityState.MEDIUM, 0.0, 0.0

            # 计算真实波动幅度
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift(1))
            low_close = np.abs(df['low'] - df['close'].shift(1))

            true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = true_range.rolling(window=self.atr_period).mean().iloc[-1]

            # 计算ATR百分比 (相对于价格)
            current_price = df['close'].iloc[-1]
            atr_pct = atr / current_price if current_price > 0 else 0.0

            # 分类波动率状态 (atr_pct已经是百分比，如0.00345表示0.345%)
            if atr_pct < self.volatility_threshold_low * 0.01:  # 0.5% -> 0.005
                return VolatilityState.LOW, float(atr), float(atr_pct)
            elif atr_pct > self.volatility_threshold_high * 0.01:
                return VolatilityState.HIGH, float(atr), float(atr_pct)
            else:
                return VolatilityState.MEDIUM, float(atr), float(atr_pct)

        except Exception as e:
            logger.error(f"❌ 波动率分类失败: {e}")
            return VolatilityState.MEDIUM, 0.0, 0.0

    def _classify_trend(self, df: pd.DataFrame) -> Tuple[TrendState, float, float]:
        """分类趋势状态"""
        try:
            if len(df) < self.trend_lookback_periods:
                return TrendState.RANGING, 0.0, 0.0

            # 计算价格变化
            prices = df['close'].values[-self.trend_lookback_periods:]
            start_price = prices[0]
            end_price = prices[-1]

            # 计算趋势方向和强度
            price_change_pct = (end_price - start_price) / start_price if start_price > 0 else 0.0
            trend_direction = np.sign(price_change_pct)

            # 计算趋势强度 (使用线性回归斜率)
            x = np.arange(len(prices))
            slope, intercept = np.polyfit(x, prices, 1)
            trend_strength = abs(slope) / np.mean(prices) if np.mean(prices) > 0 else 0.0

            # 分类趋势状态
            trend_threshold = 0.001  # 0.1%作为趋势阈值

            if price_change_pct > trend_threshold:
                return TrendState.UPTREND, float(trend_strength), float(trend_direction)
            elif price_change_pct < -trend_threshold:
                return TrendState.DOWNTREND, float(trend_strength), float(trend_direction)
            else:
                return TrendState.RANGING, float(trend_strength), float(trend_direction)

        except Exception as e:
            logger.error(f"❌ 趋势分类失败: {e}")
            return TrendState.RANGING, 0.0, 0.0

    def _assess_liquidity(self,
                         df: pd.DataFrame,
                         tick_data: Optional[List[Dict[str, Any]]]) -> Tuple[LiquidityState, Dict[str, Any]]:
        """评估流动性状态"""
        try:
            volume_metrics = {}

            # 基于K线数据的流动性评估
            if df is not None and not df.empty:
                recent_volume = df['volume'].tail(20)
                avg_volume = recent_volume.mean()
                volume_std = recent_volume.std()

                volume_metrics.update({
                    'avg_volume': float(avg_volume),
                    'volume_std': float(volume_std),
                    'volume_to_price_ratio': float(avg_volume / df['close'].iloc[-1]) if df['close'].iloc[-1] > 0 else 0.0
                })

                # 简单分类
                if avg_volume < 100:  # 这个阈值需要根据具体品种调整
                    liquidity_state = LiquidityState.THIN
                elif avg_volume < 1000:
                    liquidity_state = LiquidityState.NORMAL
                else:
                    liquidity_state = LiquidityState.LIQUID
            else:
                liquidity_state = LiquidityState.NORMAL

            # 如果有tick数据，进行更精确的流动性评估
            if tick_data and len(tick_data) > 10:
                tick_sizes = [t.get('size', 0) for t in tick_data[-50:]]
                avg_tick_size = np.mean(tick_sizes) if tick_sizes else 0.0

                volume_metrics['avg_tick_size'] = float(avg_tick_size)
                volume_metrics['tick_count'] = len(tick_data)

            return liquidity_state, volume_metrics

        except Exception as e:
            logger.error(f"❌ 流动性评估失败: {e}")
            return LiquidityState.NORMAL, {}

    def _get_trading_session(self) -> TradingSession:
        """获取当前交易时段"""
        try:
            import datetime
            utc_now = datetime.datetime.utcnow()
            utc_hour = utc_now.hour

            # 简化分类
            if 0 <= utc_hour < 8:
                return TradingSession.ASIA
            elif 8 <= utc_hour < 16:
                return TradingSession.EUROPE
            elif 16 <= utc_hour < 24:
                return TradingSession.US
            else:
                return TradingSession.US

        except Exception as e:
            logger.error(f"❌ 交易时段获取失败: {e}")
            return TradingSession.EUROPE

    def _determine_market_state(self,
                               volatility_state: VolatilityState,
                               trend_state: TrendState,
                               liquidity_state: LiquidityState,
                               trading_session: TradingSession) -> str:
        """确定整体市场状态"""
        # 组合分析
        if volatility_state == VolatilityState.HIGH and trend_state != TrendState.RANGING:
            return "TRENDING_HIGH_VOL"
        elif volatility_state == VolatilityState.LOW and trend_state == TrendState.RANGING:
            return "RANGING_LOW_VOL"
        elif volatility_state == VolatilityState.HIGH and trend_state == TrendState.RANGING:
            return "VOLATILE_RANGING"
        elif liquidity_state == LiquidityState.THIN:
            return "LOW_LIQUIDITY"
        elif trading_session == TradingSession.OVERLAP:
            return "HIGH_ACTIVITY"
        else:
            return "NORMAL"

    def _calculate_environment_score(self,
                                    volatility_state: VolatilityState,
                                    trend_state: TrendState,
                                    liquidity_state: LiquidityState,
                                    market_state: str) -> float:
        """计算环境分数 (0-100)"""
        score = 50.0  # 基础分数

        # 波动率调整
        if volatility_state == VolatilityState.LOW:
            score += 10  # 低波动率通常更稳定
        elif volatility_state == VolatilityState.HIGH:
            score -= 15  # 高波动率风险更高

        # 趋势调整
        if trend_state == TrendState.UPTREND:
            score += 5  # 上涨趋势有利
        elif trend_state == TrendState.DOWNTREND:
            score -= 5  # 下跌趋势不利

        # 流动性调整
        if liquidity_state == LiquidityState.LIQUID:
            score += 10  # 高流动性有利
        elif liquidity_state == LiquidityState.THIN:
            score -= 15  # 低流动性风险高

        # 市场状态调整
        if market_state == "RANGING_LOW_VOL":
            score += 5
        elif market_state == "TRENDING_HIGH_VOL":
            score -= 10
        elif market_state == "LOW_LIQUIDITY":
            score -= 20

        # 限制在0-100范围内
        return max(0.0, min(100.0, score))

    def _get_default_environment(self) -> Dict[str, Any]:
        """获取默认环境分析结果"""
        return {
            'volatility_state': VolatilityState.MEDIUM.value,
            'volatility_details': {'atr_value': 0.0, 'atr_pct': 0.0, 'state': VolatilityState.MEDIUM.value},
            'trend_state': TrendState.RANGING.value,
            'trend_details': {'strength': 0.0, 'direction': 0.0, 'state': TrendState.RANGING.value},
            'liquidity_state': LiquidityState.NORMAL.value,
            'liquidity_details': {},
            'trading_session': TradingSession.EUROPE.value,
            'market_state': 'NORMAL',
            'environment_score': 50.0,
            'timestamp': time.time(),
            'is_high_volatility': False,
            'is_low_volatility': False,
            'is_uptrend': False,
            'is_downtrend': False,
            'is_ranging': True
        }

    def get_validation_parameters(self, environment: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据市场环境获取验证参数

        参数:
            environment: 市场环境分析结果

        返回:
            dict: 验证参数
        """
        try:
            volatility_state = environment.get('volatility_state', 'MEDIUM')
            environment_score = environment.get('environment_score', 50.0)
            market_state = environment.get('market_state', 'NORMAL')

            # 基础参数
            params = {
                'cvd_threshold': 0.7,
                'value_area_strictness': 0.8,
                'min_value_area_strength': 60.0,
                'orderflow_strictness': 0.8,
                'multi_tf_strictness': 0.8,
                'validation_mode': 'NORMAL'
            }

            # 根据波动率状态调整
            if volatility_state == 'HIGH':
                params.update({
                    'cvd_threshold': 0.6,  # 降低CVD阈值
                    'value_area_strictness': 0.7,  # 降低价值区间严格度
                    'min_value_area_strength': 50.0,  # 降低最小强度要求
                    'validation_mode': 'HIGH_VOLATILITY'
                })
            elif volatility_state == 'LOW':
                params.update({
                    'cvd_threshold': 0.8,  # 提高CVD阈值
                    'value_area_strictness': 0.9,  # 提高价值区间严格度
                    'min_value_area_strength': 70.0,  # 提高最小强度要求
                    'validation_mode': 'LOW_VOLATILITY'
                })

            # 根据环境分数调整
            if environment_score < 40:
                # 环境差，收紧验证
                params.update({
                    'orderflow_strictness': 0.9,
                    'multi_tf_strictness': 0.9
                })
            elif environment_score > 70:
                # 环境好，放宽验证
                params.update({
                    'orderflow_strictness': 0.7,
                    'multi_tf_strictness': 0.7
                })

            # 根据市场状态调整
            if market_state == 'LOW_LIQUIDITY':
                params.update({
                    'cvd_threshold': 0.5,  # 大幅降低CVD阈值
                    'validation_mode': 'LOW_LIQUIDITY'
                })
            elif market_state == 'TRENDING_HIGH_VOL':
                params.update({
                    'value_area_strictness': 0.6,  # 趋势中价值区间不那么重要
                    'validation_mode': 'TRENDING'
                })

            logger.debug(f"🔧 [MarketEnvironment] 验证参数: {params}")
            return params

        except Exception as e:
            logger.error(f"❌ 验证参数获取失败: {e}")
            return {
                'cvd_threshold': 0.7,
                'value_area_strictness': 0.8,
                'min_value_area_strength': 60.0,
                'orderflow_strictness': 0.8,
                'multi_tf_strictness': 0.8,
                'validation_mode': 'NORMAL'
            }

    def get_trading_recommendations(self, environment: Dict[str, Any]) -> List[str]:
        """
        获取交易建议

        参数:
            environment: 市场环境分析结果

        返回:
            list: 交易建议列表
        """
        recommendations = []

        volatility_state = environment.get('volatility_state', 'MEDIUM')
        trend_state = environment.get('trend_state', 'RANGING')
        liquidity_state = environment.get('liquidity_state', 'NORMAL')
        environment_score = environment.get('environment_score', 50.0)

        # 波动率建议
        if volatility_state == 'HIGH':
            recommendations.append("高波动率环境：减小仓位，扩大止损")
        elif volatility_state == 'LOW':
            recommendations.append("低波动率环境：耐心等待，寻找突破机会")

        # 趋势建议
        if trend_state == 'UPTREND':
            recommendations.append("上涨趋势：优先考虑多头信号")
        elif trend_state == 'DOWNTREND':
            recommendations.append("下跌趋势：优先考虑空头信号")
        else:
            recommendations.append("震荡市场：关注区间边界")

        # 流动性建议
        if liquidity_state == 'THIN':
            recommendations.append("流动性稀薄：警惕滑点，减小仓位")

        # 环境分数建议
        if environment_score < 40:
            recommendations.append("环境评分低：保守交易或观望")
        elif environment_score > 70:
            recommendations.append("环境评分高：积极寻找机会")

        return recommendations

    def clear_cache(self):
        """清除缓存"""
        self._last_analysis = None
        self._last_analysis_time = 0
        logger.debug("🧹 [MarketEnvironment] 缓存已清除")

    def get_history_stats(self) -> Dict[str, Any]:
        """获取历史统计信息"""
        if not self.history:
            return {'history_size': 0}

        try:
            # 提取历史分数
            scores = [h.get('environment_score', 50.0) for h in self.history]
            volatility_states = [h.get('volatility_state', 'MEDIUM') for h in self.history]
            trend_states = [h.get('trend_state', 'RANGING') for h in self.history]

            return {
                'history_size': len(self.history),
                'avg_environment_score': float(np.mean(scores)) if scores else 50.0,
                'std_environment_score': float(np.std(scores)) if len(scores) > 1 else 0.0,
                'high_volatility_percentage': float(sum(1 for s in volatility_states if s == 'HIGH') / len(volatility_states) * 100) if volatility_states else 0.0,
                'uptrend_percentage': float(sum(1 for s in trend_states if s == 'UPTREND') / len(trend_states) * 100) if trend_states else 0.0,
                'downtrend_percentage': float(sum(1 for s in trend_states if s == 'DOWNTREND') / len(trend_states) * 100) if trend_states else 0.0
            }

        except Exception as e:
            logger.error(f"❌ 历史统计获取失败: {e}")
            return {'history_size': len(self.history)}


# 工厂函数
def create_market_environment_analyzer(config: Dict[str, Any] = None) -> MarketEnvironmentAnalyzer:
    """创建市场环境分析器"""
    if config is None:
        config = {}

    return MarketEnvironmentAnalyzer(
        volatility_threshold_low=config.get('volatility_threshold_low', 0.5),
        volatility_threshold_high=config.get('volatility_threshold_high', 2.0),
        trend_lookback_periods=config.get('trend_lookback_periods', 20),
        atr_period=config.get('atr_period', 14)
    )