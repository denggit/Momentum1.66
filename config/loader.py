#!/usr/bin/env python
# -*- coding: utf-8 -*-
import copy
import logging
import os
import re
# 为了类型提示
from typing import Dict, Any, Union

import yaml


def load_strategy_config(strategy_name: str, symbol: str) -> dict:
    """
    根据策略名称和交易对，动态加载专属配置文件。
    示例: strategy_name="smc", symbol="ETH-USDT-SWAP"
    将读取: config/smc/ETH-USDT-SWAP.yaml
    """
    # ==================== 安全验证 ====================
    # 1. 验证strategy_name只包含字母、数字和下划线
    if not re.match(r'^[a-zA-Z0-9_]+$', strategy_name):
        raise ValueError(f"无效的策略名称: {strategy_name}，只允许字母、数字和下划线")

    # 2. 验证symbol格式 (交易对格式: XXX-XXX-XXX 或 XXX-XXX-XXX-XXX)
    # 允许的格式如: ETH-USDT-SWAP, BTC-USDT-SWAP, SOL-USDT-SWAP, DOGE-USDT-SWAP, ETH-USDT-SWAP-SCALPER
    symbol_pattern = r'^[A-Z0-9]+(?:-[A-Z0-9]+){2,3}$'  # 2-3个连字符，即3或4个部分

    # 3. 处理文件扩展名并验证基本名称
    if symbol.endswith('.yaml'):
        # 如果已经包含.yaml扩展名，提取基本名称
        symbol_base = symbol[:-5]  # 移除".yaml"
        # 验证基本名称格式
        if not re.match(symbol_pattern, symbol_base):
            raise ValueError(f"无效的交易对符号: {symbol_base}，预期格式: XXX-XXX-XXX 或 XXX-XXX-XXX-XXX (大写字母和数字)")
        symbol_with_ext = symbol  # 保持原样
    else:
        # 验证原始symbol格式
        if not re.match(symbol_pattern, symbol):
            raise ValueError(f"无效的交易对符号: {symbol}，预期格式: XXX-XXX-XXX 或 XXX-XXX-XXX-XXX (大写字母和数字)")
        symbol_base = symbol
        symbol_with_ext = f"{symbol}.yaml"

    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, strategy_name, symbol_with_ext)

    # 4. 路径规范化安全检查
    normalized_path = os.path.normpath(config_path)
    # 确保规范化后的路径仍在config目录下
    if not normalized_path.startswith(current_dir):
        logging.error(f"❌ 路径遍历攻击检测: {symbol_with_ext}")
        raise ValueError(f"安全违规: 尝试访问config目录外的文件")

    # 5. 检查文件是否存在
    if not os.path.exists(normalized_path):
        logging.error(f"⚠️ 找不到配置文件: {normalized_path}")
        raise FileNotFoundError(f"Missing config for {symbol} under {strategy_name}")

    with open(normalized_path, 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)

    return config


# 如果系统还有地方强依赖旧的 settings.yaml (如 API Key)，可以保留这部分：
def load_global_settings():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    settings_path = os.path.join(current_dir, 'settings.yaml')
    if os.path.exists(settings_path):
        with open(settings_path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
    return {}


GLOBAL_SETTINGS = load_global_settings()


def _deep_update(target: dict, source: dict) -> dict:
    """递归合并source字典到target字典，source值覆盖target值"""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target


# 如果系统需要全局默认配置，可以在这里定义
ORDERFLOW_DEFAULT_CONFIG = {
    "symbol": "",
    "contract": {
        "contract_size": 0.1
    },
    "trading": {
        "leverage": 50,
        "risk_pct": 0.8,
        "email_cooldown": 600,
        "scan_interval": 0.1
    },
    "orderflow": {
        "armed_threshold_usdt": 5_000_000,
        "fire_cooldown_sec": 300,
        "recent_stop_loss_window": 900,
        "rebound_threshold": 1.005,
        "patience_latency": 3600,
        "price_silence_threshold": 0.5,
        "price_drop_threshold": 0.06,
        "safe_drop_min": 0.005,
        "dump_anomaly_threshold": 1.5,
        "resistance_anomaly_threshold": 4.0,
        "v_reversal_dump_threshold": 1.2,
        "v_reversal_counter_threshold": 500_000,
        "v_reversal_rebound_ratio": 0.08,
        "v_reversal_min_rebound": 0.05,
        "v_reversal_max_rebound": 0.25,
        "broad_report_threshold": 150_000,
        "broad_min_bounce": 0.03,
        "broad_max_bounce": 0.30,
        "wall_threshold_usdt": 8_000_000,
        "wall_max_drop_pct": 0.08,
        "squeeze_buy_threshold": 5_000_000,
        "squeeze_price_change": 0.08,
        "snapshot_interval_seconds": 10,
        "snapshot_window_minutes": 5,
        "snapshot_count": 30,
        "analysis_snapshot_count": 18,
        "memory_decay_factor": 0.9,
        "memory_update_factor": 0.1,
        "memory_update_threshold_m": 2.0
    },
    "execution": {
        "tp1_pct": 0.004,
        "tp2_pct": 0.012,
        "sl_pct": 0.0015,
        "anti_slide_threshold": 4_000_000,
        # 生命周期参数
        "breakeven_pct": 0.0015,  # 保本价上浮0.15%（考虑手续费）
        "mech_step1_trigger_pct": 0.008,
        "mech_step1_sl_pct": 0.004,
        "wall_sl_offset_pct": 0.0005,
        "moonbag_warning_ratio": 0.75,
        "fallback_threshold_pct": 0.002,
        "min_move_pct": 0.001,
        "moon_strong_candle_pct": 0.002,
        "moon_sl_offset_pct": 0.0005,
        "stage0_interval": 1.0,
        "stage1_interval": 2.0,
        "stage2_interval": 2.0,
        "stage3_interval": 5.0
    },
    "smc_validation": {
        "enabled": True,
        "timeframes": ["5m", "15m", "1H"]
    }
}


def load_orderflow_config(symbol: str, return_dict: bool = False) -> Union[Dict[str, Any], 'OrderFlowConfig']:
    """
    加载订单流策略配置，并进行基本验证。

    参数:
        symbol: 交易对符号，如"ETH-USDT-SWAP"
        return_dict: 是否返回字典格式（向后兼容），默认返回OrderFlowConfig对象

    返回:
        配置字典或OrderFlowConfig对象
    """
    try:
        config = load_strategy_config("orderflow", symbol)
    except FileNotFoundError:
        logging.error(f"找不到订单流配置文件: config/orderflow/{symbol}.yaml")
        raise

    # 创建默认配置的深拷贝
    default_config = copy.deepcopy(ORDERFLOW_DEFAULT_CONFIG)

    # 递归合并配置到默认值（用户配置覆盖默认值）
    merged_config = _deep_update(default_config, config)

    # 确保symbol字段正确
    merged_config["symbol"] = symbol

    # 验证必需字段
    required_fields = ["contract", "trading", "orderflow", "execution"]
    for field in required_fields:
        if field not in merged_config or not isinstance(merged_config[field], dict):
            logging.warning(f"⚠️  配置缺少字段或类型错误: {field}, 使用默认值")
            merged_config[field] = ORDERFLOW_DEFAULT_CONFIG.get(field, {})

    # 从全局配置获取合约面值映射
    global_contract_values = GLOBAL_SETTINGS.get("contract_values", {})
    if not global_contract_values:
        # 如果全局配置中没有，使用默认值
        global_contract_values = {
            "ETH-USDT-SWAP": 0.1,
            "BTC-USDT-SWAP": 0.01,
            "SOL-USDT-SWAP": 1.0,
            "DOGE-USDT-SWAP": 100.0
        }
        logging.warning("⚠️  全局配置中未找到合约面值映射，使用默认值")

    # 验证合约面值
    if symbol in global_contract_values:
        contract_size = global_contract_values[symbol]
        # 确保contract.contract_size与全局映射一致
        if "contract" in merged_config and "contract_size" in merged_config["contract"]:
            if merged_config["contract"]["contract_size"] != contract_size:
                logging.warning(
                    f"⚠️  合约面值不一致: 配置中为{merged_config['contract']['contract_size']}, 全局映射中为{contract_size}, 使用全局值")
                merged_config["contract"]["contract_size"] = contract_size
        else:
            merged_config.setdefault("contract", {})["contract_size"] = contract_size
    else:
        logging.error(f"❌ 全局合约面值映射中找不到交易对: {symbol}")
        # 使用配置中的值或默认值
        if "contract" in merged_config and "contract_size" in merged_config["contract"]:
            logging.warning(f"  使用配置中的合约面值: {merged_config['contract']['contract_size']}")
        else:
            merged_config.setdefault("contract", {})["contract_size"] = 0.1
            logging.warning(f"  使用默认合约面值: 0.1")

    logging.info(f"✅ 成功加载订单流配置: {symbol}")

    # 根据参数决定返回类型
    if return_dict:
        return merged_config
    else:
        # 延迟导入以避免循环依赖
        from src.strategy.orderflow.orderflow_config import OrderFlowConfig
        return OrderFlowConfig.from_dict(merged_config)


# Triple-A策略默认配置
TRIPLE_A_DEFAULT_CONFIG = {
    "symbol": "",
    "contract": {
        "contract_size": 0.1
    },
    "trading": {
        "leverage": 20,
        "risk_pct": 0.05,
        "max_daily_trades": 1000
    },
    "triple_a": {
        # Absorption（吸收）检测参数
        "absorption_price_threshold": 0.001,
        "absorption_volume_ratio": 2.0,
        "absorption_window_seconds": 30,
        "absorption_score_threshold": 0.7,
        # Accumulation（累积）检测参数
        "accumulation_width_pct": 0.003,
        "accumulation_min_ticks": 50,
        "accumulation_window_seconds": 120,
        "accumulation_score_threshold": 0.6,
        # Aggression（侵略）检测参数
        "aggression_volume_spike": 3.0,
        "aggression_breakout_pct": 0.002,
        "aggression_score_threshold": 0.75,
        # Failed Auction（失败拍卖）检测参数
        "failed_auction": {
            "window_seconds": 300,
            "detection_threshold": 0.65,
            "volume_confirmation_multiplier": 1.5
        }
    },
    "execution": {
        "entry_slippage": 0.0005,
        "initial_sl_pct": 0.01,      # 最大允许总风险1%（价格风险+手续费）
        "min_reward_ratio": 2.5
    },
    "risk_management": {
        "max_position_limit": 100,
        "min_trade_unit": 1,
        "high_volatility_threshold": 2.0,
        "max_leverage": 20,
        "margin_safety_factor": 0.8
    },
    "research": {
        "mode": "simulation",
        "output_dir": "data/triple_a_research",
        "parameter_experiments": [],
        "simulation": {
            "initial_balance": 20.0,
            "risk_per_trade": 0.05,
            "commission_rate": 0.001
        }
    }
}


def load_triple_a_config(symbol: str, return_dict: bool = False):
    """
    加载Triple-A策略配置，并进行基本验证。

    参数:
        symbol: 交易对符号，如"ETH-USDT-SWAP"
        return_dict: 是否返回字典格式（向后兼容），默认返回TripleAConfig对象

    返回:
        配置字典或TripleAConfig对象
    """
    try:
        config = load_strategy_config("triple_a", symbol)
    except FileNotFoundError:
        logging.error(f"找不到Triple-A配置文件: config/triple_a/{symbol}.yaml")
        raise

    # 创建默认配置的深拷贝
    default_config = copy.deepcopy(TRIPLE_A_DEFAULT_CONFIG)

    # 递归合并配置到默认值（用户配置覆盖默认值）
    merged_config = _deep_update(default_config, config)

    # 确保symbol字段正确
    merged_config["symbol"] = symbol

    # 验证必需字段
    required_fields = ["contract", "trading", "triple_a", "execution", "risk_management", "research"]
    for field in required_fields:
        if field not in merged_config or not isinstance(merged_config[field], dict):
            logging.warning(f"⚠️  配置缺少字段或类型错误: {field}, 使用默认值")
            merged_config[field] = TRIPLE_A_DEFAULT_CONFIG.get(field, {})

    # 从全局配置获取合约面值映射
    global_contract_values = GLOBAL_SETTINGS.get("contract_values", {})
    if not global_contract_values:
        # 如果全局配置中没有，使用默认值
        global_contract_values = {
            "ETH-USDT-SWAP": 0.1,
            "BTC-USDT-SWAP": 0.01,
            "SOL-USDT-SWAP": 1.0,
            "DOGE-USDT-SWAP": 100.0
        }
        logging.warning("⚠️  全局配置中未找到合约面值映射，使用默认值")

    # 验证合约面值
    if symbol in global_contract_values:
        contract_size = global_contract_values[symbol]
        # 确保contract.contract_size与全局映射一致
        if "contract" in merged_config and "contract_size" in merged_config["contract"]:
            if merged_config["contract"]["contract_size"] != contract_size:
                logging.warning(
                    f"⚠️  合约面值不一致: 配置中为{merged_config['contract']['contract_size']}, 全局映射中为{contract_size}, 使用全局值")
                merged_config["contract"]["contract_size"] = contract_size
        else:
            merged_config.setdefault("contract", {})["contract_size"] = contract_size
    else:
        logging.error(f"❌ 全局合约面值映射中找不到交易对: {symbol}")
        # 使用配置中的值或默认值
        if "contract" in merged_config and "contract_size" in merged_config["contract"]:
            logging.warning(f"  使用配置中的合约面值: {merged_config['contract']['contract_size']}")
        else:
            merged_config.setdefault("contract", {})["contract_size"] = 0.1
            logging.warning(f"  使用默认合约面值: 0.1")

    logging.info(f"✅ 成功加载Triple-A配置: {symbol}")

    # 根据参数决定返回类型
    if return_dict:
        return merged_config
    else:
        # 延迟导入以避免循环依赖
        from src.strategy.triple_a.config import TripleAConfig
        return TripleAConfig.from_dict(merged_config)
