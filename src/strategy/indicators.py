#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/26/26 9:08 PM
@File       : indicators.py
@Description: 
"""
import logging

import pandas as pd
import pandas_ta as ta


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

        # 5. 计算大级别环境过滤器 (200 EMA)
        df['EMA_200'] = ta.ema(close=df['close'], length=200)

        # 6. 【新增核心动量过滤】计算 ADX (14)
        # pandas_ta 的 adx 会返回一个包含多列的 DataFrame，我们只需要 ADX_14 这一列
        adx_df = ta.adx(high=df['high'], low=df['low'], close=df['close'], length=14)
        if adx_df is not None:
            df['ADX'] = adx_df['ADX_14']
        else:
            df['ADX'] = 0.0  # 防错处理

        # 剔除因为计算均线产生的 NaN 行
        df.dropna(inplace=True)

        return df

    except Exception as e:
        logging.error(f"计算技术指标时发生错误: {e}")
        raise e


def add_smc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    SMC 波段猎手专供：大趋势判定与动能基准
    """
    import pandas_ta as ta

    # 用 144 均线作为多空分水岭 (近似 4H 级别的趋势线)
    df['EMA_144'] = ta.ema(df['close'], length=144)
    # ATR 用于识别真正的“动能突破 K 线”
    df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)

    df.dropna(inplace=True)
    return df
