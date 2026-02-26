#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/26/26 9:40 PM
@File       : loader.py
@Description: 
"""
import yaml


def load_config():
    with open('config/settings.yaml', 'r') as file:
        settings = yaml.safe_load(file)
    with open('config/strategies.yaml', 'r') as file:
        strategies = yaml.safe_load(file)
    return settings, strategies


settings, strategies = load_config()

# settings.yaml 配置
SYMBOL = settings['symbol']
TIMEFRAME = settings['timeframe']
TIMEZONE = settings['timezone']
RISK_PARAMS = settings['risk']

# strategies.yaml 配置
SQZ_PARAMS = strategies['squeeze']
