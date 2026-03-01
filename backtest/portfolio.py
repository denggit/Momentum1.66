#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from backtest.engine import run_universal_backtest
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy
from config.loader import load_strategy_config  # 【引入新加载器】

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(message)s')

START_DATE = '2021-01-01'
END_DATE = '2025-12-31'
STRATEGY_NAME = 'smc'  # 定义当前跑的策略矩阵

PORTFOLIO = [
    'ETH-USDT-SWAP',
    'BTC-USDT-SWAP',
    'SOL-USDT-SWAP',
    'DOGE-USDT-SWAP'
]

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print(f" 🌍 启动宏观矩阵: {STRATEGY_NAME.upper()} 多品种猎杀编队")
    print("=" * 70)

    for symbol in PORTFOLIO:
        print(f"\n\n>>>>>>>>>> 正在轰炸标的: {symbol} <<<<<<<<<<")

        # 1. 动态加载该币种的专属配置
        try:
            cfg = load_strategy_config(STRATEGY_NAME, symbol)
        except FileNotFoundError:
            print(f"⏩ 跳过 {symbol}: 没有找到 config/{STRATEGY_NAME}/{symbol}.yaml")
            continue

        timeframe = cfg.get('timeframe', '1H')
        strat_cfg = cfg.get('strategy', {})
        engine_cfg = cfg.get('engine', {})

        # 2. 拉取数据
        loader = OKXDataLoader(symbol=symbol, timeframe=timeframe)
        df = loader.fetch_data_by_date_range(START_DATE, END_DATE)

        if not df.empty:
            # 3. 挂载指标
            df = add_smc_indicators(df)

            # 4. 注入该币种专属的信号参数
            strategy = SMCStrategy(
                ema_period=strat_cfg.get('ema_period', 144),
                lookback=strat_cfg.get('lookback', 15),
                atr_mult=strat_cfg.get('atr_mult', 1.5),
                ob_expiry=strat_cfg.get('ob_expiry', 72),
                sl_buffer=strat_cfg.get('sl_buffer', 0.6),
                entry_buffer=strat_cfg.get('entry_buffer', -0.1)
            )
            df = strategy.generate_signals(df)

            # 5. 注入该币种专属的风控参数
            run_universal_backtest(
                df=df,
                strategy_name=f"SMC ({symbol})",
                symbol=symbol,
                initial_capital=engine_cfg.get('initial_capital', 1000.0),
                max_risk=engine_cfg.get('max_risk', 0.02),
                atr_multiplier=engine_cfg.get('atr_multiplier', 7.0),
                fee_rate=engine_cfg.get('fee_rate', 0.0005)
            )
        else:
            print(f"⚠️ {symbol} 在指定时间段内无数据。")