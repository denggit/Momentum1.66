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


def add_reversal_indicators(df: pd.DataFrame, bb_len=20, bb_std=2.5, rsi_len=14) -> pd.DataFrame:
    """
    二号引擎专供：计算极端布林带 (2.5标准差) 和 RSI 超买超卖
    """
    import pandas_ta as ta

    # 计算极宽的布林带（捕捉极端情绪插针）
    bbands = ta.bbands(close=df['close'], length=bb_len, std=bb_std)
    df['BB_lower_rev'] = bbands[bbands.filter(like='BBL').columns[0]]
    df['BB_upper_rev'] = bbands[bbands.filter(like='BBU').columns[0]]

    # 计算 RSI
    df['RSI'] = ta.rsi(close=df['close'], length=rsi_len)

    # 依然需要 ATR 作为止损参考
    df['ATR'] = ta.atr(high=df['high'], low=df['low'], close=df['close'], length=14)

    df.dropna(inplace=True)
    return df


def add_macd_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    二号引擎专供：MACD 动能背离指标
    """
    import pandas_ta as ta

    # 1. 计算标准 MACD (12, 26, 9)
    macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
    # pandas_ta 默认生成的列名很长，我们把它拼接到 df 并重命名
    df = pd.concat([df, macd], axis=1)
    df.rename(columns={
        'MACD_12_26_9': 'MACD',
        'MACDh_12_26_9': 'MACD_hist',
        'MACDs_12_26_9': 'MACD_signal'
    }, inplace=True)

    # 2. 加入一条 EMA_20 用来判断当前的大趋势（必须在跌势中找背离）
    df['EMA_20'] = ta.ema(df['close'], length=20)

    # 3. 保留 ATR 用于止损
    df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)

    df.dropna(inplace=True)
    return df
