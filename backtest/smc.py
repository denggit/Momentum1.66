#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from backtest.engine import run_universal_backtest
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy
from config.loader import SYMBOL

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

START_DATE = '2020-01-01'
END_DATE = '2026-02-27'
SMC_TIMEFRAME = '1H'  # 波段交易，回归 1H 大气层！

if __name__ == "__main__":
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=SMC_TIMEFRAME)
    df = loader.fetch_historical_data(limit=100000)

    if not df.empty:
        df = df[(df.index >= START_DATE) & (df.index <= END_DATE)]

        if df.empty:
            print(f"数据为空！")
        else:
            # 1. 挂载 SMC 需要的均线和 ATR
            df = add_smc_indicators(df)

            # 2. 生成聪明的订单块回踩信号
            strategy = SMCStrategy(ema_period=144, lookback=15, atr_mult=1.5)
            df = strategy.generate_signals(df)

            # 3. 呼叫全能引擎！
            # 进场：SMC 左侧挂单回踩
            # 出场：极其广阔的 4.5 倍 ATR 吊灯追踪，一口吃穿整个趋势！
            run_universal_backtest(
                df=df,
                strategy_name="SMC 聪明钱波段猎手 (1H Order Block)",
                initial_capital=1000.0,
                max_risk=0.02,
                atr_multiplier=4.5,  # 沿用一号引擎神级参数，死死咬住波段
                target_r=None,  # 不设止盈，利润奔跑
                fee_rate=0.0005  # Taker 手续费 (因为我们是在K线收盘确认触发)
            )