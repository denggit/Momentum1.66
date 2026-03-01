#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/1/26 8:06 PM
@File       : export_history_k.py
@Description: 
"""
import os

from src.data_feed.okx_loader import OKXDataLoader

# 获取项目根目录下的 data/reports 目录
current_file = os.path.abspath(__file__)
# 向上推三层：report.py -> utils -> src -> 根目录 (Momentum1.66)
project_root = os.path.dirname(os.path.dirname(current_file))
# 使用项目根目录下的 data/reports 目录
data_dir = os.path.join(project_root, 'data', 'history_k')
os.makedirs(data_dir, exist_ok=True)

if __name__ == "__main__":
    symbol = "ETH-USDT-SWAP"
    timeframe = "1H"
    start_str = "2020-01-01"
    end_str = "2025-12-31"
    data_loader = OKXDataLoader(symbol, timeframe)
    df = data_loader.fetch_data_by_date_range(start_str, end_str)
    output_file = os.path.join(data_dir, f"{symbol}_{timeframe}.csv")
    df.to_csv(output_file)
