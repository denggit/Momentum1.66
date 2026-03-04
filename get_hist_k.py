#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26 8:20 PM
@File       : get_hist_k.py
@Description: 
"""
from src.data_feed.okx_loader import OKXDataLoader


if __name__ == "__main__":
    loader = OKXDataLoader(symbol="ETH-USDT-SWAP", timeframe='1m')
    df = loader.fetch_from_okx(limit=200)
    df.to_csv("hist_k.csv")
