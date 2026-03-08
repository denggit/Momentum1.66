#!/usr/bin/env python
# -*- coding: utf-8 -*-
import copy
import logging
import os
# 为了类型提示
from typing import Dict, Any, Union

import yaml


def load_strategy_config(strategy_name: str, symbol: str) -> dict:
    """
    根据策略名称和交易对，动态加载专属配置文件。
    示例: strategy_name="smc", symbol="ETH-USDT-SWAP"
    将读取: config/smc/ETH-USDT-SWAP.yaml
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, strategy_name, f"{symbol}.yaml")

    if not os.path.exists(config_path):
        logging.error(f"⚠️ 找不到配置文件: {config_path}")
        raise FileNotFoundError(f"Missing config for {symbol} under {strategy_name}")

    with open(config_path, 'r', encoding='utf-8') as file:
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
        from src.strategy.orderflow_config import OrderFlowConfig
        return OrderFlowConfig.from_dict(merged_config)
