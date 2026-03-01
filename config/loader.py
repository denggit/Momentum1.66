#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import yaml
import logging


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
