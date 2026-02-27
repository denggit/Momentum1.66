#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import yaml


def load_config():
    # 获取当前 loader.py 文件所在的绝对目录 (即 config/ 目录)
    current_dir = os.path.dirname(os.path.abspath(__file__))

    settings_path = os.path.join(current_dir, 'settings.yaml')
    strategies_path = os.path.join(current_dir, 'strategies.yaml')

    with open(settings_path, 'r') as file:
        settings = yaml.safe_load(file)
    with open(strategies_path, 'r') as file:
        strategies = yaml.safe_load(file)

    return settings, strategies


settings, strategies = load_config()

# settings.yaml 配置
SYMBOL = settings['symbol']
TIMEFRAME = settings['timeframe']
TIMEZONE = settings['timezone']
RISK_PARAMS = settings['risk']
FEE_RATE = 0.0005  # 如果 settings 里没配，可以写死在这里或者去 settings 加上

# strategies.yaml 配置
SQZ_PARAMS = strategies['squeeze']