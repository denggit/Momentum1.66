#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from backtest.engine import run_universal_backtest
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy

# 调低日志级别，让终端输出清爽一点，直接看最终报表
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(message)s')

START_DATE = '2021-01-01'
END_DATE = '2026-02-27'
SMC_TIMEFRAME = '1H'

# 多品种矩阵四大天王
PORTFOLIO = [
    'ETH-USDT-SWAP',
    'BTC-USDT-SWAP',
    'SOL-USDT-SWAP',
    'DOGE-USDT-SWAP'
]

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print(" 🌍 启动宏观矩阵: 聪明钱多品种猎杀编队 (Portfolio Matrix)")
    print(" 核心参数: 深度刺穿(-0.1) | 宽容防守(0.6) | 终极追踪(7.0x)")
    print("=" * 70)

    for symbol in PORTFOLIO:
        print(f"\n\n>>>>>>>>>> 正在轰炸标的: {symbol} <<<<<<<<<<")
        loader = OKXDataLoader(symbol=symbol, timeframe=SMC_TIMEFRAME)
        df = loader.fetch_data_by_date_range(START_DATE, END_DATE)

        if not df.empty:
            # 1. 挂载指标
            df = add_smc_indicators(df)

            # 2. 注入你的神级参数！
            strategy = SMCStrategy(
                ema_period=144,
                lookback=15,
                atr_mult=1.5,
                ob_expiry=72,
                sl_buffer=0.6,  # <--- 你的 0.6 终极防线
                entry_buffer=-0.1  # <--- 你的 -0.1 深度刺穿
            )
            df = strategy.generate_signals(df)

            # 3. 呼叫全能引擎！每个币种分配独立的 1000 刀初始资金测试它的威力
            run_universal_backtest(
                df=df,
                strategy_name=f"SMC 终极装甲版 ({symbol})",
                initial_capital=1000.0,
                max_risk=0.02,
                atr_multiplier=7.0,  # 宇宙级厚尾追踪
                target_r=None,
                fee_rate=0.0005
            )
        else:
            print(f"⚠️ {symbol} 在指定时间段内无数据。")