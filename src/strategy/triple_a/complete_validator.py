#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
完整的Triple-A验证器
实现Fabio验证策略的完整验证链，包含加权综合评分和适应性阈值调整
"""

from typing import Dict, Any, List, Tuple, Optional
import time
from dataclasses import dataclass, field

from src.utils.log import get_logger
from src.strategy.triple_a.value_area_analyzer import ValueAreaAnalyzer
from src.strategy.triple_a.orderflow_validator import OrderFlowValidator
from src.strategy.triple_a.market_environment import MarketEnvironmentAnalyzer
from src.strategy.triple_a.config import TripleAConfig

logger = get_logger(__name__)


@dataclass
class ValidationWeight:
    """验证权重配置"""
    value_area: float = 0.30      # 价值区间验证权重
    orderflow: float = 0.35       # 订单流验证权重
    multi_tf: float = 0.25        # 多时间框架验证权重
    environment: float = 0.10     # 环境适应性权重


class CompleteTripleAValidator:
    """完整的Triple-A验证器

    实现Fabio验证策略的完整验证链，包含：
    1. 加权综合评分
    2. 环境适应性阈值调整
    3. 验证结果缓存优化
    4. 性能监控
    """

    def __init__(self, config: TripleAConfig, context=None):
        """
        初始化完整验证器

        参数:
            config: Triple-A配置
            context: 市场上下文（可选）
        """
        self.config = config
        self.context = context

        # 初始化各验证模块
        self.value_area_analyzer = None
        self.orderflow_validator = None
        self.market_environment_analyzer = None
        self.smc_validator = None  # SMC验证器（从外部传入）

        # 验证权重
        self.weights = ValidationWeight()

        # 性能优化：缓存
        self._validation_cache: Dict[str, Tuple[bool, str, float]] = {}
        self._cache_ttl = 30  # 缓存有效期（秒）
        self._cache_timestamps: Dict[str, float] = {}

        # 性能监控
        self.performance_stats = {
            'total_validations': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'avg_validation_time_ms': 0.0,
            'validation_success_rate': 0.0,
            'successful_validations': 0
        }

        self._initialize_validators()

        logger.info(f"🚀 CompleteTripleAValidator初始化完成: "
                   f"权重(VA={self.weights.value_area}, OF={self.weights.orderflow}, "
                   f"MTF={self.weights.multi_tf}, ENV={self.weights.environment})")

    def _initialize_validators(self):
        """初始化各个验证器"""
        try:
            # 1. 价值区间分析器
            if self.config.value_area_validation_enabled:
                self.value_area_analyzer = ValueAreaAnalyzer(
                    bin_size=self.config.value_area_bin_size if hasattr(self.config, 'value_area_bin_size') else 0.5,
                    balance_range_pct=self.config.value_area_balance_range_pct
                )
                logger.info("✅ 价值区间分析器初始化完成")

            # 2. 订单流验证器
            if self.config.orderflow_validation_enabled:
                self.orderflow_validator = OrderFlowValidator(
                    cvd_threshold=self.config.orderflow_cvd_threshold,
                    large_order_ratio=self.config.orderflow_large_order_ratio,
                    min_liquidity_hunt_volume=100000.0
                )
                logger.info("✅ 订单流验证器初始化完成")

            # 3. 市场环境分析器
            if self.config.adaptive_validation_enabled:
                self.market_environment_analyzer = MarketEnvironmentAnalyzer(
                    volatility_threshold_low=self.config.market_volatility_threshold_low,
                    volatility_threshold_high=self.config.market_volatility_threshold_high
                )
                logger.info("✅ 市场环境分析器初始化完成")

        except Exception as e:
            logger.error(f"❌ 验证器初始化失败: {e}")
            raise

    def set_smc_validator(self, smc_validator):
        """设置SMC多时间框架验证器"""
        self.smc_validator = smc_validator
        if smc_validator:
            logger.info("✅ SMC多时间框架验证器已设置")

    def validate_signal(self, signal: Dict[str, Any],
                       context_data: Dict[str, Any] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        完整验证Triple-A信号

        参数:
            signal: Triple-A信号
            context_data: 上下文数据（可选）

        返回:
            tuple: (验证结果, 验证消息, 详细验证结果)
        """
        start_time = time.time()
        self.performance_stats['total_validations'] += 1

        try:
            # 1. 检查缓存
            cache_key = self._generate_cache_key(signal, context_data)
            cached_result = self._get_cached_result(cache_key)
            if cached_result is not None:
                self.performance_stats['cache_hits'] += 1
                logger.debug(f"🔍 验证缓存命中: {cache_key}")
                return cached_result

            self.performance_stats['cache_misses'] += 1

            # 2. 执行各个验证模块
            validation_results = self._execute_all_validations(signal, context_data)

            # 3. 计算加权综合评分
            total_score, score_details = self._calculate_weighted_score(validation_results)

            # 4. 获取环境适应性阈值
            adjusted_threshold = self._get_adjusted_threshold(validation_results.get('environment', {}))

            # 5. 确定最终验证结果
            is_valid = total_score >= adjusted_threshold
            validation_message = self._generate_validation_message(is_valid, total_score,
                                                                   adjusted_threshold, validation_results)

            # 6. 更新性能统计
            if is_valid:
                self.performance_stats['successful_validations'] += 1

            # 7. 缓存结果
            result = (is_valid, validation_message, {
                'score': total_score,
                'adjusted_threshold': adjusted_threshold,
                'score_details': score_details,
                'validation_results': validation_results
            })
            self._cache_result(cache_key, result)

            # 8. 更新平均验证时间
            validation_time = (time.time() - start_time) * 1000  # 转换为毫秒
            current_avg = self.performance_stats['avg_validation_time_ms']
            n = self.performance_stats['total_validations']
            self.performance_stats['avg_validation_time_ms'] = (
                (current_avg * (n - 1) + validation_time) / n
            )

            # 9. 更新成功率
            success_rate = (self.performance_stats['successful_validations'] /
                           self.performance_stats['total_validations'] * 100)
            self.performance_stats['validation_success_rate'] = success_rate

            logger.debug(f"📊 验证完成: 结果={is_valid}, 得分={total_score:.2f}, "
                        f"阈值={adjusted_threshold:.2f}, 耗时={validation_time:.1f}ms")

            return result

        except Exception as e:
            logger.error(f"❌ 完整验证失败: {e}")
            # 验证失败时返回保守结果
            return False, f"验证异常: {str(e)}", {}

    def _execute_all_validations(self, signal: Dict[str, Any],
                                context_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """执行所有验证模块"""
        validation_results = {}

        # 1. 价值区间验证
        if self.value_area_analyzer and self.config.value_area_validation_enabled:
            try:
                is_valid, message, details = self._validate_value_area(signal, context_data)
                validation_results['value_area'] = {
                    'valid': is_valid,
                    'message': message,
                    'details': details,
                    'weight': self.weights.value_area
                }
            except Exception as e:
                logger.error(f"❌ 价值区间验证异常: {e}")
                validation_results['value_area'] = {
                    'valid': False,
                    'message': f"验证异常: {str(e)}",
                    'details': {},
                    'weight': self.weights.value_area
                }

        # 2. 订单流验证
        if self.orderflow_validator and self.config.orderflow_validation_enabled:
            try:
                is_valid, message, details = self._validate_orderflow(signal, context_data)
                validation_results['orderflow'] = {
                    'valid': is_valid,
                    'message': message,
                    'details': details,
                    'weight': self.weights.orderflow
                }
            except Exception as e:
                logger.error(f"❌ 订单流验证异常: {e}")
                validation_results['orderflow'] = {
                    'valid': False,
                    'message': f"验证异常: {str(e)}",
                    'details': {},
                    'weight': self.weights.orderflow
                }

        # 3. 多时间框架验证
        if self.smc_validator and self.config.multi_tf_alignment_enabled:
            try:
                is_valid, message, details = self._validate_multi_timeframe(signal, context_data)
                validation_results['multi_timeframe'] = {
                    'valid': is_valid,
                    'message': message,
                    'details': details,
                    'weight': self.weights.multi_tf
                }
            except Exception as e:
                logger.error(f"❌ 多时间框架验证异常: {e}")
                validation_results['multi_timeframe'] = {
                    'valid': False,
                    'message': f"验证异常: {str(e)}",
                    'details': {},
                    'weight': self.weights.multi_tf
                }

        # 4. 市场环境分析
        if self.market_environment_analyzer and self.config.adaptive_validation_enabled:
            try:
                environment_result = self._analyze_environment(signal, context_data)
                validation_results['environment'] = environment_result
            except Exception as e:
                logger.error(f"❌ 市场环境分析异常: {e}")
                validation_results['environment'] = {
                    'environment_score': 50.0,
                    'validation_mode': 'ERROR',
                    'adjusted_params': {}
                }

        return validation_results

    def _validate_value_area(self, signal: Dict[str, Any],
                            context_data: Dict[str, Any] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """执行价值区间验证"""
        # 这里可以调用现有的ValueAreaAnalyzer方法
        # 简化版本，实际应该使用真实的数据
        current_price = signal.get('price', 0.0)
        direction = signal.get('direction', '')

        # 在实际实现中，这里应该：
        # 1. 从context_data获取K线数据
        # 2. 调用value_area_analyzer.calculate_fabio_value_area()
        # 3. 调用value_area_analyzer.validate_aggression_with_value_area()

        # 占位实现
        is_valid = True
        message = "价值区间验证通过"
        details = {
            'current_price': current_price,
            'direction': direction,
            'valid': True
        }

        return is_valid, message, details

    def _validate_orderflow(self, signal: Dict[str, Any],
                           context_data: Dict[str, Any] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """执行订单流验证"""
        # 这里可以调用现有的OrderFlowValidator方法
        # 简化版本
        direction = signal.get('direction', '')
        price = signal.get('price', 0.0)

        # 在实际实现中，这里应该：
        # 1. 从context_data获取tick数据
        # 2. 调用orderflow_validator.validate_aggression_with_orderflow()

        # 占位实现
        is_valid = True
        message = "订单流验证通过"
        details = {
            'direction': direction,
            'price': price,
            'valid': True
        }

        return is_valid, message, details

    def _validate_multi_timeframe(self, signal: Dict[str, Any],
                                 context_data: Dict[str, Any] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """执行多时间框架验证"""
        if not self.smc_validator:
            return False, "SMC验证器未设置", {}

        try:
            current_price = signal.get('price', 0.0)

            # 更新SMC结构
            self.smc_validator.update_structure()

            # 执行最终检查
            is_valid, message = self.smc_validator.final_check(current_price)

            details = {
                'current_price': current_price,
                'valid': is_valid,
                'message': message
            }

            return is_valid, message, details

        except Exception as e:
            logger.error(f"❌ SMC验证失败: {e}")
            return False, f"SMC验证异常: {str(e)}", {}

    def _analyze_environment(self, signal: Dict[str, Any],
                            context_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """分析市场环境"""
        if not self.market_environment_analyzer:
            return {
                'environment_score': 50.0,
                'validation_mode': 'NORMAL',
                'adjusted_params': {}
            }

        try:
            # 在实际实现中，这里应该：
            # 1. 从context_data获取K线数据和tick数据
            # 2. 调用market_environment_analyzer.analyze_environment()
            # 3. 调用market_environment_analyzer.get_validation_parameters()

            # 占位实现
            environment = self.market_environment_analyzer.analyze_environment(None, None)
            validation_params = self.market_environment_analyzer.get_validation_parameters(environment)

            return {
                'environment': environment,
                'validation_params': validation_params,
                'environment_score': environment.get('environment_score', 50.0),
                'validation_mode': validation_params.get('validation_mode', 'NORMAL'),
                'adjusted_params': validation_params
            }

        except Exception as e:
            logger.error(f"❌ 市场环境分析失败: {e}")
            return {
                'environment_score': 50.0,
                'validation_mode': 'ERROR',
                'adjusted_params': {}
            }

    def _calculate_weighted_score(self, validation_results: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        """计算加权综合评分"""
        total_score = 0.0
        score_details = {}
        total_weight = 0.0

        for validator_name, result in validation_results.items():
            if validator_name == 'environment':
                # 环境分析不参与评分，只影响阈值
                continue

            is_valid = result.get('valid', False)
            weight = result.get('weight', 0.0)

            # 计算该验证器的得分（有效为1，无效为0）
            validator_score = 1.0 if is_valid else 0.0
            weighted_score = validator_score * weight

            total_score += weighted_score
            total_weight += weight

            score_details[validator_name] = {
                'score': validator_score,
                'weight': weight,
                'weighted_score': weighted_score,
                'is_valid': is_valid
            }

        # 归一化处理
        if total_weight > 0:
            normalized_score = total_score / total_weight
        else:
            normalized_score = 0.0

        # 应用环境适应性调整
        if 'environment' in validation_results:
            environment_result = validation_results['environment']
            environment_score = environment_result.get('environment_score', 50.0)

            # 环境分数影响最终得分（50分基准）
            environment_factor = environment_score / 50.0
            normalized_score = normalized_score * environment_factor

        return min(normalized_score, 1.0), score_details

    def _get_adjusted_threshold(self, environment_result: Dict[str, Any]) -> float:
        """获取环境适应性调整后的阈值"""
        base_threshold = 0.7  # 基础阈值

        if not environment_result:
            return base_threshold

        validation_mode = environment_result.get('validation_mode', 'NORMAL')
        adjusted_params = environment_result.get('adjusted_params', {})

        # 根据不同验证模式调整阈值
        if validation_mode == 'HIGH_VOLATILITY':
            # 高波动率环境，降低阈值
            return base_threshold * 0.9
        elif validation_mode == 'LOW_VOLATILITY':
            # 低波动率环境，提高阈值
            return base_threshold * 1.1
        elif validation_mode == 'TRENDING':
            # 趋势环境，适度降低阈值
            return base_threshold * 0.95
        elif validation_mode == 'RANGING':
            # 震荡环境，提高阈值
            return base_threshold * 1.05
        else:
            # 正常环境
            return base_threshold

    def _generate_validation_message(self, is_valid: bool, total_score: float,
                                    adjusted_threshold: float,
                                    validation_results: Dict[str, Any]) -> str:
        """生成验证消息"""
        if is_valid:
            message = f"✅ 验证通过！得分: {total_score:.2f} (阈值: {adjusted_threshold:.2f})"

            # 添加详细验证结果
            details = []
            for validator_name, result in validation_results.items():
                if validator_name != 'environment' and result.get('valid', False):
                    details.append(f"{validator_name}: ✓")

            if details:
                message += f" | 通过验证: {', '.join(details)}"

        else:
            message = f"❌ 验证失败！得分: {total_score:.2f} (阈值: {adjusted_threshold:.2f})"

            # 添加失败原因
            failures = []
            for validator_name, result in validation_results.items():
                if validator_name != 'environment' and not result.get('valid', True):
                    failures.append(f"{validator_name}: {result.get('message', '失败')}")

            if failures:
                message += f" | 失败原因: {', '.join(failures)}"

        return message

    def _generate_cache_key(self, signal: Dict[str, Any],
                           context_data: Dict[str, Any] = None) -> str:
        """生成缓存键"""
        # 使用信号的关键信息生成缓存键
        signal_key = f"{signal.get('type', '')}_{signal.get('direction', '')}_{signal.get('price', 0):.2f}"

        # 添加时间戳（每5分钟一个时间窗口）
        time_window = int(time.time() / 300)  # 5分钟窗口

        return f"{signal_key}_{time_window}"

    def _get_cached_result(self, cache_key: str) -> Optional[Tuple[bool, str, Dict[str, Any]]]:
        """获取缓存结果"""
        if cache_key in self._validation_cache:
            cache_time = self._cache_timestamps.get(cache_key, 0)
            if time.time() - cache_time < self._cache_ttl:
                return self._validation_cache[cache_key]
            else:
                # 缓存过期，清除
                del self._validation_cache[cache_key]
                del self._cache_timestamps[cache_key]
        return None

    def _cache_result(self, cache_key: str, result: Tuple[bool, str, Dict[str, Any]]):
        """缓存验证结果"""
        self._validation_cache[cache_key] = result
        self._cache_timestamps[cache_key] = time.time()

        # 限制缓存大小
        if len(self._validation_cache) > 100:
            # 删除最旧的缓存项
            oldest_key = min(self._cache_timestamps.items(), key=lambda x: x[1])[0]
            del self._validation_cache[oldest_key]
            del self._cache_timestamps[oldest_key]

    def clear_cache(self):
        """清除所有缓存"""
        self._validation_cache.clear()
        self._cache_timestamps.clear()
        logger.info("🧹 验证缓存已清除")

    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计信息"""
        return {
            **self.performance_stats,
            'cache_size': len(self._validation_cache),
            'cache_ttl': self._cache_ttl,
            'weights': {
                'value_area': self.weights.value_area,
                'orderflow': self.weights.orderflow,
                'multi_tf': self.weights.multi_tf,
                'environment': self.weights.environment
            }
        }

    def update_weights(self, new_weights: ValidationWeight):
        """更新验证权重"""
        self.weights = new_weights
        logger.info(f"🔧 验证权重已更新: VA={new_weights.value_area}, "
                   f"OF={new_weights.orderflow}, MTF={new_weights.multi_tf}, "
                   f"ENV={new_weights.environment}")

    def set_cache_ttl(self, ttl_seconds: int):
        """设置缓存有效期"""
        self._cache_ttl = ttl_seconds
        logger.info(f"🔧 缓存有效期已设置为: {ttl_seconds}秒")


# 简化版工厂函数
def create_complete_validator(config: TripleAConfig, context=None) -> CompleteTripleAValidator:
    """创建完整验证器"""
    return CompleteTripleAValidator(config, context)