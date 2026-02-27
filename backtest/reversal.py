#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from backtest.engine import run_universal_backtest
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_reversal_indicators
from src.strategy.reversal import ReversalStrategy
from config.loader import SYMBOL

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

START_DATE = '2020-01-01'
END_DATE = '2026-02-27'

# 【极其关键】反转策略必须降维打击，强制使用 15m 级别！
REV_TIMEFRAME = '15m'

if __name__ == "__main__":
    # 拉取 15m 级别的数据（为了覆盖 2022 年大熊市，我们拉取 20万 根）
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=REV_TIMEFRAME)
    df = loader.fetch_historical_data(limit=200000)

    if not df.empty:
        df = df[(df.index >= START_DATE) & (df.index <= END_DATE)]

        if df.empty:
            print(f"数据截取后为空，请检查时间范围。")
        else:
            # 1. 加载反转指标 (2.5 倍宽幅布林带，捕捉极端插针)
            df = add_reversal_indicators(df, bb_len=20, bb_std=2.5, rsi_len=14)

            # 2. 生成反转信号 (RSI 极度超卖/超买)
            strategy = ReversalStrategy(rsi_oversold=30, rsi_overbought=70)
            df = strategy.generate_signals(df)

            # 3. 呼叫全能引擎！
            # 呼叫全能引擎！
            run_universal_backtest(
                df=df,
                strategy_name="Reversal 极值反转 (15m)",
                initial_capital=1000.0,
                max_risk=0.02,
                atr_multiplier=1.5,  # 【极紧止损】冒最小的险
                target_r=1.5  # 【强制止盈】赚了 1.5 倍立刻落袋为安！
            )