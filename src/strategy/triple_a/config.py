#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Triple-A 策略配置类
将分散的配置参数统一管理，简化TripleADetector的初始化
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


@dataclass
class TripleAConfig:
    """Triple-A 策略配置类"""

    # ==================== 基本配置 ====================
    symbol: str = ""
    contract_size: float = 0.1  # 合约面值

    # ==================== 交易参数 ====================
    leverage: int = 20
    risk_pct: float = 0.05
    max_daily_trades: int = 1000

    # ==================== Triple-A检测参数 ====================
    # Absorption（吸收）检测参数
    absorption_price_threshold: float = 0.001      # 价格阈值0.1%
    absorption_volume_ratio: float = 2.0           # 成交量比率
    absorption_window_seconds: int = 30            # 吸收检测窗口
    absorption_score_threshold: float = 0.7        # 吸收置信度阈值

    # Accumulation（累积）检测参数
    accumulation_width_pct: float = 0.003          # 累积区间宽度0.3%
    accumulation_min_ticks: int = 50               # 最小tick数
    accumulation_window_seconds: int = 120         # 累积检测窗口
    accumulation_score_threshold: float = 0.6      # 累积置信度阈值

    # Aggression（侵略）检测参数
    aggression_volume_spike: float = 3.0           # 成交量爆发倍数
    aggression_breakout_pct: float = 0.002         # 突破阈值0.2%
    aggression_score_threshold: float = 0.75       # 侵略置信度阈值

    # ==================== Fabio验证参数 ====================
    # 价值区间验证参数
    value_area_balance_range_pct: float = 0.02     # 平衡区间范围 ±2%
    value_area_ratio: float = 0.7                  # 价值区间成交量占比 70%
    min_value_area_strength: float = 60.0          # 最小价值区间强度分数
    value_area_validation_enabled: bool = True     # 是否启用价值区间验证

    # 订单流验证参数
    orderflow_cvd_threshold: float = 0.7           # CVD方向验证阈值
    orderflow_large_order_ratio: float = 2.0       # 大单比率阈值
    orderflow_validation_enabled: bool = True      # 是否启用订单流验证

    # 多时间框架验证参数
    multi_tf_alignment_enabled: bool = True        # 是否启用多时间框架对齐验证
    multi_tf_timeframes: List[str] = field(default_factory=lambda: ["5m", "15m", "1H"])  # 多时间框架列表

    # 市场环境参数
    market_volatility_threshold_high: float = 2.0  # 高波动率阈值 (ATR倍数)
    market_volatility_threshold_low: float = 0.5   # 低波动率阈值 (ATR倍数)
    adaptive_validation_enabled: bool = True       # 是否启用适应性验证

    # Failed Auction（失败拍卖）检测参数
    failed_auction_window_seconds: int = 300       # 检测窗口（5分钟）
    failed_auction_detection_threshold: float = 0.65  # 检测阈值
    failed_auction_volume_confirmation_multiplier: float = 1.5  # 成交量确认倍数

    # ==================== 执行参数 ====================
    entry_slippage: float = 0.0005                # 入场滑点容忍0.05%
    initial_sl_pct: float = 0.01                  # 最大允许总风险1%（价格风险+手续费），用于结构止损筛选
    min_reward_ratio: float = 2.5                 # 最小风险回报比（覆盖手续费后仍保持2:1净回报）

    # ==================== 风险管理参数 ====================
    max_position_limit: int = 100                 # 最大持仓数量（张）
    min_trade_unit: int = 1                       # 最小交易单位（张）
    high_volatility_threshold: float = 2.0        # 高波动率阈值（ATR倍数）
    max_leverage: int = 20                        # 最大杠杆倍数（可配置）
    margin_safety_factor: float = 0.8             # 保证金安全系数（80%）

    # ==================== 科考船研究参数 ====================
    research_mode: str = "simulation"             # collection, simulation, parameter_experiment
    research_output_dir: str = "data/triple_a_research"
    research_initial_balance: float = 20.0
    research_risk_per_trade: float = 0.05
    research_commission_rate: float = 0.001      # 总手续费0.1%（买入0.05% + 卖出0.05%）

    # ==================== 参数实验配置 ====================
    parameter_experiments: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'TripleAConfig':
        """从配置字典创建配置对象"""
        if not config_dict:
            return cls()

        # 提取各层级配置
        contract_config = config_dict.get("contract", {})
        trading_config = config_dict.get("trading", {})
        triple_a_config = config_dict.get("triple_a", {})
        execution_config = config_dict.get("execution", {})
        risk_config = config_dict.get("risk_management", {})
        research_config = config_dict.get("research", {})

        # 创建配置实例
        config = cls()
        config.symbol = config_dict.get("symbol", "")

        # 合约配置
        config.contract_size = contract_config.get("contract_size", config.contract_size)

        # 交易配置
        config.leverage = trading_config.get("leverage", config.leverage)
        config.risk_pct = trading_config.get("risk_pct", config.risk_pct)
        config.max_daily_trades = trading_config.get("max_daily_trades", config.max_daily_trades)

        # Triple-A配置
        config.absorption_price_threshold = triple_a_config.get(
            "absorption_price_threshold", config.absorption_price_threshold)
        config.absorption_volume_ratio = triple_a_config.get(
            "absorption_volume_ratio", config.absorption_volume_ratio)
        config.absorption_window_seconds = triple_a_config.get(
            "absorption_window_seconds", config.absorption_window_seconds)
        config.absorption_score_threshold = triple_a_config.get(
            "absorption_score_threshold", config.absorption_score_threshold)

        config.accumulation_width_pct = triple_a_config.get(
            "accumulation_width_pct", config.accumulation_width_pct)
        config.accumulation_min_ticks = triple_a_config.get(
            "accumulation_min_ticks", config.accumulation_min_ticks)
        config.accumulation_window_seconds = triple_a_config.get(
            "accumulation_window_seconds", config.accumulation_window_seconds)
        config.accumulation_score_threshold = triple_a_config.get(
            "accumulation_score_threshold", config.accumulation_score_threshold)

        config.aggression_volume_spike = triple_a_config.get(
            "aggression_volume_spike", config.aggression_volume_spike)
        config.aggression_breakout_pct = triple_a_config.get(
            "aggression_breakout_pct", config.aggression_breakout_pct)
        config.aggression_score_threshold = triple_a_config.get(
            "aggression_score_threshold", config.aggression_score_threshold)

        # Fabio验证配置
        validation_config = triple_a_config.get("validation", {})
        config.value_area_balance_range_pct = validation_config.get(
            "value_area_balance_range_pct", config.value_area_balance_range_pct)
        config.value_area_ratio = validation_config.get(
            "value_area_ratio", config.value_area_ratio)
        config.min_value_area_strength = validation_config.get(
            "min_value_area_strength", config.min_value_area_strength)
        config.value_area_validation_enabled = validation_config.get(
            "value_area_validation_enabled", config.value_area_validation_enabled)

        config.orderflow_cvd_threshold = validation_config.get(
            "orderflow_cvd_threshold", config.orderflow_cvd_threshold)
        config.orderflow_large_order_ratio = validation_config.get(
            "orderflow_large_order_ratio", config.orderflow_large_order_ratio)
        config.orderflow_validation_enabled = validation_config.get(
            "orderflow_validation_enabled", config.orderflow_validation_enabled)

        config.multi_tf_alignment_enabled = validation_config.get(
            "multi_tf_alignment_enabled", config.multi_tf_alignment_enabled)
        config.multi_tf_timeframes = validation_config.get(
            "multi_tf_timeframes", config.multi_tf_timeframes)

        config.market_volatility_threshold_high = validation_config.get(
            "market_volatility_threshold_high", config.market_volatility_threshold_high)
        config.market_volatility_threshold_low = validation_config.get(
            "market_volatility_threshold_low", config.market_volatility_threshold_low)
        config.adaptive_validation_enabled = validation_config.get(
            "adaptive_validation_enabled", config.adaptive_validation_enabled)

        # Failed Auction配置
        failed_auction_config = triple_a_config.get("failed_auction", {})
        config.failed_auction_window_seconds = failed_auction_config.get(
            "window_seconds", config.failed_auction_window_seconds)
        config.failed_auction_detection_threshold = failed_auction_config.get(
            "detection_threshold", config.failed_auction_detection_threshold)
        config.failed_auction_volume_confirmation_multiplier = failed_auction_config.get(
            "volume_confirmation_multiplier", config.failed_auction_volume_confirmation_multiplier)

        # 执行配置
        config.entry_slippage = execution_config.get("entry_slippage", config.entry_slippage)
        config.initial_sl_pct = execution_config.get("initial_sl_pct", config.initial_sl_pct)
        config.min_reward_ratio = execution_config.get("min_reward_ratio", config.min_reward_ratio)

        # 风险管理配置
        config.max_position_limit = risk_config.get("max_position_limit", config.max_position_limit)
        config.min_trade_unit = risk_config.get("min_trade_unit", config.min_trade_unit)
        config.high_volatility_threshold = risk_config.get(
            "high_volatility_threshold", config.high_volatility_threshold)
        config.max_leverage = risk_config.get("max_leverage", config.max_leverage)
        config.margin_safety_factor = risk_config.get("margin_safety_factor", config.margin_safety_factor)

        # 科考船研究配置
        config.research_mode = research_config.get("mode", config.research_mode)
        config.research_output_dir = research_config.get("output_dir", config.research_output_dir)

        # 处理模拟配置（支持两种结构：扁平结构和嵌套结构）
        # 先检查是否有simulation子节点
        simulation_config = research_config.get("simulation", {})
        if simulation_config:
            # 嵌套结构：research.simulation.initial_balance
            config.research_initial_balance = simulation_config.get(
                "initial_balance", config.research_initial_balance)
            config.research_risk_per_trade = simulation_config.get("risk_per_trade", config.research_risk_per_trade)
            config.research_commission_rate = simulation_config.get(
                "commission_rate", config.research_commission_rate)
        else:
            # 扁平结构：research.initial_balance
            config.research_initial_balance = research_config.get(
                "initial_balance", config.research_initial_balance)
            config.research_risk_per_trade = research_config.get("risk_per_trade", config.research_risk_per_trade)
            config.research_commission_rate = research_config.get(
                "commission_rate", config.research_commission_rate)

        # 参数实验配置
        config.parameter_experiments = research_config.get("parameter_experiments", [])

        logger.debug(f"[TripleAConfig] 配置加载完成: {config.symbol}")
        return config

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（用于JSON序列化或向后兼容）"""
        return {
            "symbol": self.symbol,
            "contract": {"contract_size": self.contract_size},
            "trading": {
                "leverage": self.leverage,
                "risk_pct": self.risk_pct,
                "max_daily_trades": self.max_daily_trades
            },
            "triple_a": {
                "absorption_price_threshold": self.absorption_price_threshold,
                "absorption_volume_ratio": self.absorption_volume_ratio,
                "absorption_window_seconds": self.absorption_window_seconds,
                "absorption_score_threshold": self.absorption_score_threshold,
                "accumulation_width_pct": self.accumulation_width_pct,
                "accumulation_min_ticks": self.accumulation_min_ticks,
                "accumulation_window_seconds": self.accumulation_window_seconds,
                "accumulation_score_threshold": self.accumulation_score_threshold,
                "aggression_volume_spike": self.aggression_volume_spike,
                "aggression_breakout_pct": self.aggression_breakout_pct,
                "aggression_score_threshold": self.aggression_score_threshold,
                "validation": {
                    "value_area_balance_range_pct": self.value_area_balance_range_pct,
                    "value_area_ratio": self.value_area_ratio,
                    "min_value_area_strength": self.min_value_area_strength,
                    "value_area_validation_enabled": self.value_area_validation_enabled,
                    "orderflow_cvd_threshold": self.orderflow_cvd_threshold,
                    "orderflow_large_order_ratio": self.orderflow_large_order_ratio,
                    "orderflow_validation_enabled": self.orderflow_validation_enabled,
                    "multi_tf_alignment_enabled": self.multi_tf_alignment_enabled,
                    "multi_tf_timeframes": self.multi_tf_timeframes,
                    "market_volatility_threshold_high": self.market_volatility_threshold_high,
                    "market_volatility_threshold_low": self.market_volatility_threshold_low,
                    "adaptive_validation_enabled": self.adaptive_validation_enabled
                },
                "failed_auction": {
                    "window_seconds": self.failed_auction_window_seconds,
                    "detection_threshold": self.failed_auction_detection_threshold,
                    "volume_confirmation_multiplier": self.failed_auction_volume_confirmation_multiplier
                }
            },
            "execution": {
                "entry_slippage": self.entry_slippage,
                "initial_sl_pct": self.initial_sl_pct,
                "min_reward_ratio": self.min_reward_ratio
            },
            "risk_management": {
                "max_position_limit": self.max_position_limit,
                "min_trade_unit": self.min_trade_unit,
                "high_volatility_threshold": self.high_volatility_threshold,
                "max_leverage": self.max_leverage,
                "margin_safety_factor": self.margin_safety_factor
            },
            "research": {
                "mode": self.research_mode,
                "output_dir": self.research_output_dir,
                "parameter_experiments": self.parameter_experiments,
                "simulation": {
                    "initial_balance": self.research_initial_balance,
                    "risk_per_trade": self.research_risk_per_trade,
                    "commission_rate": self.research_commission_rate
                }
            }
        }

    def __str__(self) -> str:
        """简洁的字符串表示"""
        return f"TripleAConfig(symbol={self.symbol}, contract_size={self.contract_size}, " \
               f"leverage={self.leverage}x, risk_pct={self.risk_pct*100}%)"