#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OrderFlow策略配置类
将分散的配置参数统一管理，简化OrderFlowMath的初始化
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class OrderFlowConfig:
    """订单流策略配置类"""

    # ==================== 基本配置 ====================
    symbol: str = ""
    contract_size: float = 0.1  # 合约面值

    # ==================== 交易参数 ====================
    leverage: int = 50
    risk_pct: float = 0.8
    email_cooldown: int = 600
    scan_interval: float = 0.1

    # ==================== 订单流核心参数 ====================
    # 状态机参数
    armed_threshold_usdt: float = 5_000_000
    fire_cooldown_sec: int = 300

    # 智能空间拦截参数
    recent_stop_loss_window: int = 900
    rebound_threshold: float = 1.005

    # 耐心潜伏参数
    patience_latency: int = 3600
    price_silence_threshold: float = 0.5

    # 冰山吸收条件
    price_drop_threshold: float = 0.06
    safe_drop_min: float = 0.005  # 最小安全跌幅阈值
    dump_anomaly_threshold: float = 1.5
    resistance_anomaly_threshold: float = 4.0

    # V型反转条件
    v_reversal_dump_threshold: float = 1.2
    v_reversal_counter_threshold: float = 500_000
    v_reversal_rebound_ratio: float = 0.08
    v_reversal_min_rebound: float = 0.05
    v_reversal_max_rebound: float = 0.25

    # 宽口径汇报条件
    broad_report_threshold: float = 150_000
    broad_min_bounce: float = 0.03
    broad_max_bounce: float = 0.30

    # 隐形墙探测参数
    wall_threshold_usdt: float = 8_000_000
    wall_max_drop_pct: float = 0.08

    # 空头挤压探测参数
    squeeze_buy_threshold: float = 5_000_000
    squeeze_price_change: float = 0.08

    # ==================== 执行参数 ====================
    tp1_pct: float = 0.004
    tp2_pct: float = 0.012
    sl_pct: float = 0.0015
    anti_slide_threshold: float = 4_000_000

    # ==================== 快照参数 ====================
    snapshot_interval_seconds: int = 10  # 快照间隔秒数
    snapshot_window_minutes: int = 5  # 快照窗口分钟数
    snapshot_count: int = 30  # 快照数量 (snapshot_window_minutes * 60 / snapshot_interval_seconds)
    analysis_snapshot_count: int = 18  # 分析所需快照数量 (3分钟)

    # ==================== 记忆参数 ====================
    memory_decay_factor: float = 0.9  # 记忆衰减因子 (旧记忆权重)
    memory_update_factor: float = 0.1  # 记忆更新因子 (新记忆权重)
    memory_update_threshold_m: float = 2.0  # 记忆更新阈值 (单位：百万美元)

    # ==================== SMC验证参数 ====================
    smc_validation_enabled: bool = True
    smc_timeframes: list = field(default_factory=lambda: ["5m", "15m", "1H"])

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'OrderFlowConfig':
        """从配置字典创建配置对象"""
        if not config_dict:
            return cls()

        # 提取各层级配置
        contract_config = config_dict.get("contract", {})
        trading_config = config_dict.get("trading", {})
        orderflow_config = config_dict.get("orderflow", {})
        execution_config = config_dict.get("execution", {})
        smc_config = config_dict.get("smc_validation", {})

        # 创建配置实例
        config = cls()
        config.symbol = config_dict.get("symbol", "")

        # 合约配置
        config.contract_size = contract_config.get("contract_size", config.contract_size)

        # 交易配置
        config.leverage = trading_config.get("leverage", config.leverage)
        config.risk_pct = trading_config.get("risk_pct", config.risk_pct)
        config.email_cooldown = trading_config.get("email_cooldown", config.email_cooldown)
        config.scan_interval = trading_config.get("scan_interval", config.scan_interval)

        # 订单流配置
        config.armed_threshold_usdt = abs(orderflow_config.get("armed_threshold_usdt", config.armed_threshold_usdt))
        config.fire_cooldown_sec = orderflow_config.get("fire_cooldown_sec", config.fire_cooldown_sec)
        config.recent_stop_loss_window = orderflow_config.get("recent_stop_loss_window", config.recent_stop_loss_window)
        config.rebound_threshold = orderflow_config.get("rebound_threshold", config.rebound_threshold)
        config.patience_latency = orderflow_config.get("patience_latency", config.patience_latency)
        config.price_silence_threshold = orderflow_config.get("price_silence_threshold", config.price_silence_threshold)
        config.price_drop_threshold = orderflow_config.get("price_drop_threshold", config.price_drop_threshold)
        config.safe_drop_min = orderflow_config.get("safe_drop_min", config.safe_drop_min)
        config.dump_anomaly_threshold = orderflow_config.get("dump_anomaly_threshold", config.dump_anomaly_threshold)
        config.resistance_anomaly_threshold = orderflow_config.get("resistance_anomaly_threshold", config.resistance_anomaly_threshold)
        config.v_reversal_dump_threshold = orderflow_config.get("v_reversal_dump_threshold", config.v_reversal_dump_threshold)
        config.v_reversal_counter_threshold = orderflow_config.get("v_reversal_counter_threshold", config.v_reversal_counter_threshold)
        config.v_reversal_rebound_ratio = orderflow_config.get("v_reversal_rebound_ratio", config.v_reversal_rebound_ratio)
        config.v_reversal_min_rebound = orderflow_config.get("v_reversal_min_rebound", config.v_reversal_min_rebound)
        config.v_reversal_max_rebound = orderflow_config.get("v_reversal_max_rebound", config.v_reversal_max_rebound)
        config.broad_report_threshold = orderflow_config.get("broad_report_threshold", config.broad_report_threshold)
        config.broad_min_bounce = orderflow_config.get("broad_min_bounce", config.broad_min_bounce)
        config.broad_max_bounce = orderflow_config.get("broad_max_bounce", config.broad_max_bounce)
        config.wall_threshold_usdt = abs(orderflow_config.get("wall_threshold_usdt", config.wall_threshold_usdt))
        config.wall_max_drop_pct = abs(orderflow_config.get("wall_max_drop_pct", config.wall_max_drop_pct))
        config.squeeze_buy_threshold = orderflow_config.get("squeeze_buy_threshold", config.squeeze_buy_threshold)
        config.squeeze_price_change = orderflow_config.get("squeeze_price_change", config.squeeze_price_change)

        # 快照参数（如果在orderflow配置中提供）
        config.snapshot_interval_seconds = orderflow_config.get("snapshot_interval_seconds", config.snapshot_interval_seconds)
        config.snapshot_window_minutes = orderflow_config.get("snapshot_window_minutes", config.snapshot_window_minutes)
        config.snapshot_count = orderflow_config.get("snapshot_count", config.snapshot_count)
        config.analysis_snapshot_count = orderflow_config.get("analysis_snapshot_count", config.analysis_snapshot_count)

        # 记忆参数（如果在orderflow配置中提供）
        config.memory_decay_factor = orderflow_config.get("memory_decay_factor", config.memory_decay_factor)
        config.memory_update_factor = orderflow_config.get("memory_update_factor", config.memory_update_factor)
        config.memory_update_threshold_m = orderflow_config.get("memory_update_threshold_m", config.memory_update_threshold_m)

        # 执行配置
        config.tp1_pct = execution_config.get("tp1_pct", config.tp1_pct)
        config.tp2_pct = execution_config.get("tp2_pct", config.tp2_pct)
        config.sl_pct = execution_config.get("sl_pct", config.sl_pct)
        config.anti_slide_threshold = execution_config.get("anti_slide_threshold", config.anti_slide_threshold)

        # SMC配置
        config.smc_validation_enabled = smc_config.get("enabled", config.smc_validation_enabled)
        config.smc_timeframes = smc_config.get("timeframes", config.smc_timeframes)

        logger.debug(f"[OrderFlowConfig] 配置加载完成: {config.symbol}")
        return config

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（用于JSON序列化或向后兼容）"""
        return {
            "symbol": self.symbol,
            "contract": {"contract_size": self.contract_size},
            "trading": {
                "leverage": self.leverage,
                "risk_pct": self.risk_pct,
                "email_cooldown": self.email_cooldown,
                "scan_interval": self.scan_interval
            },
            "orderflow": {
                "armed_threshold_usdt": self.armed_threshold_usdt,
                "fire_cooldown_sec": self.fire_cooldown_sec,
                "recent_stop_loss_window": self.recent_stop_loss_window,
                "rebound_threshold": self.rebound_threshold,
                "patience_latency": self.patience_latency,
                "price_silence_threshold": self.price_silence_threshold,
                "price_drop_threshold": self.price_drop_threshold,
                "safe_drop_min": self.safe_drop_min,
                "dump_anomaly_threshold": self.dump_anomaly_threshold,
                "resistance_anomaly_threshold": self.resistance_anomaly_threshold,
                "v_reversal_dump_threshold": self.v_reversal_dump_threshold,
                "v_reversal_counter_threshold": self.v_reversal_counter_threshold,
                "v_reversal_rebound_ratio": self.v_reversal_rebound_ratio,
                "v_reversal_min_rebound": self.v_reversal_min_rebound,
                "v_reversal_max_rebound": self.v_reversal_max_rebound,
                "broad_report_threshold": self.broad_report_threshold,
                "broad_min_bounce": self.broad_min_bounce,
                "broad_max_bounce": self.broad_max_bounce,
                "wall_threshold_usdt": self.wall_threshold_usdt,
                "wall_max_drop_pct": self.wall_max_drop_pct,
                "squeeze_buy_threshold": self.squeeze_buy_threshold,
                "squeeze_price_change": self.squeeze_price_change,
                "snapshot_interval_seconds": self.snapshot_interval_seconds,
                "snapshot_window_minutes": self.snapshot_window_minutes,
                "snapshot_count": self.snapshot_count,
                "analysis_snapshot_count": self.analysis_snapshot_count,
                "memory_decay_factor": self.memory_decay_factor,
                "memory_update_factor": self.memory_update_factor,
                "memory_update_threshold_m": self.memory_update_threshold_m
            },
            "execution": {
                "tp1_pct": self.tp1_pct,
                "tp2_pct": self.tp2_pct,
                "sl_pct": self.sl_pct,
                "anti_slide_threshold": self.anti_slide_threshold
            },
            "smc_validation": {
                "enabled": self.smc_validation_enabled,
                "timeframes": self.smc_timeframes
            }
        }

    def __str__(self) -> str:
        """简洁的字符串表示"""
        return f"OrderFlowConfig(symbol={self.symbol}, contract_size={self.contract_size}, " \
               f"armed_threshold=${self.armed_threshold_usdt/1_000_000:.1f}M, " \
               f"fire_cooldown={self.fire_cooldown_sec}s)"