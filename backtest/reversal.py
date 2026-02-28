#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from backtest.engine import run_universal_backtest
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_ema_reversal_indicators
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
            # 1. 挂载 EMA 和 Vol 探测系统
            df = add_ema_reversal_indicators(df)
            
            # 2. 生成龙抬头突破信号 (要求 2.0 倍巨量)
            strategy = ReversalStrategy(vol_multiplier=2.0)
            df = strategy.generate_signals(df)
            
            # 3. 引擎启动！
            run_universal_backtest(
                df=df, 
                strategy_name="EMA200 龙抬头 (15m 右侧主升浪猎手)", 
                initial_capital=1000.0, 
                max_risk=0.02, 
                atr_multiplier=3.0,  # 给 3.0 倍 ATR 的宽容度，让利润狂奔
                target_r=None,       # 坚决不止盈！
                fee_rate=0.0005      # Taker 费率
            )
