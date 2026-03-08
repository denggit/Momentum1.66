#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OrderFlow策略配置类
将分散的配置参数统一管理，简化OrderFlowMath的初始化
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, Any

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
    tp1_split_ratio: float = 0.5  # TP1仓位分割比例 (30%去TP1，70%去TP2)
    tp1_min_size: int = 1  # TP1最小张数

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

    # ==================== 生命周期管理参数 ====================
    # 保本参数
    breakeven_pct: float = 0.0015  # 保本价上浮比例（考虑手续费）

    # 机械阶梯防守参数
    mech_step1_trigger_pct: float = 0.008  # 阶段1触发涨幅
    mech_step1_sl_pct: float = 0.004  # 阶段1止损位置

    # 隐形墙跟随参数
    wall_sl_offset_pct: float = 0.0005  # 墙下偏移比例

    # 吹哨预警参数
    moonbag_warning_ratio: float = 0.75  # 距离TP2的比例
    fallback_threshold_pct: float = 0.002  # 回落阈值

    # 止损移动参数
    min_move_pct: float = 0.001  # 最小移动距离比例

    # 无限登月参数
    moon_strong_candle_pct: float = 0.002  # 强推力阳线阈值
    moon_sl_offset_pct: float = 0.0005  # 登月止损偏移

    # 监控间隔参数
    stage0_interval: float = 2.0  # 阶段0监控间隔
    stage1_interval: float = 4.0  # 阶段1监控间隔
    stage2_interval: float = 2.0  # 阶段2监控间隔
    stage3_interval: float = 5.0  # 阶段3监控间隔

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
        config.resistance_anomaly_threshold = orderflow_config.get("resistance_anomaly_threshold",
                                                                   config.resistance_anomaly_threshold)
        config.v_reversal_dump_threshold = orderflow_config.get("v_reversal_dump_threshold",
                                                                config.v_reversal_dump_threshold)
        config.v_reversal_counter_threshold = orderflow_config.get("v_reversal_counter_threshold",
                                                                   config.v_reversal_counter_threshold)
        config.v_reversal_rebound_ratio = orderflow_config.get("v_reversal_rebound_ratio",
                                                               config.v_reversal_rebound_ratio)
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
        config.snapshot_interval_seconds = orderflow_config.get("snapshot_interval_seconds",
                                                                config.snapshot_interval_seconds)
        config.snapshot_window_minutes = orderflow_config.get("snapshot_window_minutes", config.snapshot_window_minutes)
        config.snapshot_count = orderflow_config.get("snapshot_count", config.snapshot_count)
        config.analysis_snapshot_count = orderflow_config.get("analysis_snapshot_count", config.analysis_snapshot_count)

        # 记忆参数（如果在orderflow配置中提供）
        config.memory_decay_factor = orderflow_config.get("memory_decay_factor", config.memory_decay_factor)
        config.memory_update_factor = orderflow_config.get("memory_update_factor", config.memory_update_factor)
        config.memory_update_threshold_m = orderflow_config.get("memory_update_threshold_m",
                                                                config.memory_update_threshold_m)

        # 执行配置
        config.tp1_pct = execution_config.get("tp1_pct", config.tp1_pct)
        config.tp2_pct = execution_config.get("tp2_pct", config.tp2_pct)
        config.sl_pct = execution_config.get("sl_pct", config.sl_pct)
        config.anti_slide_threshold = execution_config.get("anti_slide_threshold", config.anti_slide_threshold)
        config.tp1_split_ratio = execution_config.get("tp1_split_ratio", config.tp1_split_ratio)
        config.tp1_min_size = execution_config.get("tp1_min_size", config.tp1_min_size)

        # 生命周期配置（可以在execution配置中提供）
        config.breakeven_pct = execution_config.get("breakeven_pct", config.breakeven_pct)
        config.mech_step1_trigger_pct = execution_config.get("mech_step1_trigger_pct", config.mech_step1_trigger_pct)
        config.mech_step1_sl_pct = execution_config.get("mech_step1_sl_pct", config.mech_step1_sl_pct)
        config.wall_sl_offset_pct = execution_config.get("wall_sl_offset_pct", config.wall_sl_offset_pct)
        config.moonbag_warning_ratio = execution_config.get("moonbag_warning_ratio", config.moonbag_warning_ratio)
        config.fallback_threshold_pct = execution_config.get("fallback_threshold_pct", config.fallback_threshold_pct)
        config.min_move_pct = execution_config.get("min_move_pct", config.min_move_pct)
        config.moon_strong_candle_pct = execution_config.get("moon_strong_candle_pct", config.moon_strong_candle_pct)
        config.moon_sl_offset_pct = execution_config.get("moon_sl_offset_pct", config.moon_sl_offset_pct)
        config.stage0_interval = execution_config.get("stage0_interval", config.stage0_interval)
        config.stage1_interval = execution_config.get("stage1_interval", config.stage1_interval)
        config.stage2_interval = execution_config.get("stage2_interval", config.stage2_interval)
        config.stage3_interval = execution_config.get("stage3_interval", config.stage3_interval)

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
                "anti_slide_threshold": self.anti_slide_threshold,
                "tp1_split_ratio": self.tp1_split_ratio,
                "tp1_min_size": self.tp1_min_size,
                # 生命周期参数
                "breakeven_pct": self.breakeven_pct,
                "mech_step1_trigger_pct": self.mech_step1_trigger_pct,
                "mech_step1_sl_pct": self.mech_step1_sl_pct,
                "wall_sl_offset_pct": self.wall_sl_offset_pct,
                "moonbag_warning_ratio": self.moonbag_warning_ratio,
                "fallback_threshold_pct": self.fallback_threshold_pct,
                "min_move_pct": self.min_move_pct,
                "moon_strong_candle_pct": self.moon_strong_candle_pct,
                "moon_sl_offset_pct": self.moon_sl_offset_pct,
                "stage0_interval": self.stage0_interval,
                "stage1_interval": self.stage1_interval,
                "stage2_interval": self.stage2_interval,
                "stage3_interval": self.stage3_interval
            },
            "smc_validation": {
                "enabled": self.smc_validation_enabled,
                "timeframes": self.smc_timeframes
            }
        }

    def __str__(self) -> str:
        """简洁的字符串表示"""
        return f"OrderFlowConfig(symbol={self.symbol}, contract_size={self.contract_size}, " \
               f"armed_threshold=${self.armed_threshold_usdt / 1_000_000:.1f}M, " \
               f"fire_cooldown={self.fire_cooldown_sec}s)"
