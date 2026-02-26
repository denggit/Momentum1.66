#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/26/26 9:40 PM
@File       : loader.py
@Description: 
"""
import yaml


def load_settings():
    with open('config/settings.yaml', 'r') as file:
        return yaml.safe_load(file)


settings = load_settings()

SYMBOL = settings['symbol']
TIMEFRAME = settings['timeframe']
TIMEZONE = settings['timezone']
