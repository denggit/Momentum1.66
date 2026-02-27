#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from backtest.engine import run_universal_backtest
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_macd_indicators
from src.strategy.reversal import ReversalStrategy
from config.loader import SYMBOL

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

START_DATE = '2020-01-01'
END_DATE = '2026-02-27'
REV_TIMEFRAME = '15m'

if __name__ == "__main__":
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=REV_TIMEFRAME)
    df = loader.fetch_historical_data(limit=200000)

    if not df.empty:
        df = df[(df.index >= START_DATE) & (df.index <= END_DATE)]

        if df.empty:
            print(f"数据为空！")
        else:
            # 1. 挂载 MACD 动能系统
            df = add_macd_indicators(df)

            # 2. 生成底背离信号
            strategy = ReversalStrategy()
            df = strategy.generate_signals(df)

            # 3. 引擎启动！放飞利润！
            run_universal_backtest(
                df=df,
                strategy_name="Reversal 极值反转 (MACD 底背离猎手)",
                initial_capital=1000.0,
                max_risk=0.02,
                atr_multiplier=2.5,  # 给 2.5 倍 ATR 应对底部震荡
                target_r=None,  # 【关键】抓到背离绝不止盈，追踪到底！
                fee_rate=0.0005  # Taker 费率
            )