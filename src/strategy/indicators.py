#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/26/26 9:08 PM
@File       : indicators.py
@Description: 
"""
import pandas as pd
import pandas_ta as ta
import logging


def add_squeeze_indicators(df: pd.DataFrame, bb_len=20, bb_std=2.0, kc_len=20, kc_mult=1.5) -> pd.DataFrame:
    """
    为 K 线数据计算布林带 (BB)、凯特纳通道 (KC) 和成交量均线
    """
    try:
        # 1. 计算布林带 (Bollinger Bands)
        # 直接调用 ta.bbands 底层方法，显式传入 df['close'] 序列
        bbands = ta.bbands(close=df['close'], length=bb_len, std=bb_std)
        bb_lower_col = bbands.filter(like='BBL').columns[0]
        bb_upper_col = bbands.filter(like='BBU').columns[0]

        df['BB_lower'] = bbands[bb_lower_col]
        df['BB_upper'] = bbands[bb_upper_col]

        # 2. 计算凯特纳通道 (Keltner Channels)
        # KC 需要最高价、最低价和收盘价
        kc = ta.kc(high=df['high'], low=df['low'], close=df['close'], length=kc_len, scalar=kc_mult)
        kc_lower_col = kc.filter(like='KCL').columns[0]
        kc_upper_col = kc.filter(like='KCU').columns[0]

        df['KC_lower'] = kc[kc_lower_col]
        df['KC_upper'] = kc[kc_upper_col]

        # 3. 计算成交量均线
        df['Vol_SMA'] = ta.sma(close=df['volume'], length=20)

        # 4. 计算 ATR (后面风控仓位计算时极其重要)
        df['ATR'] = ta.atr(high=df['high'], low=df['low'], close=df['close'], length=14)

        # 剔除因为计算均线产生的 NaN 行
        df.dropna(inplace=True)

        return df

    except Exception as e:
        logging.error(f"计算技术指标时发生错误: {e}")
        raise e