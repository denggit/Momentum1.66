#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/2/26 5:34 PM
@File       : test_robustness.py
@Description: 
"""
# !/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd
from backtest.engine import run_universal_backtest
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy
from src.utils.log import get_logger
logger = get_logger(__name__)

# ==========================================
# 填入你 Optuna 跑出来的 TOP 1 神级参数
# ==========================================
BASE_PARAMS = {
    'ema_period': 60,
    'lookback': 28,
    'atr_mult': 2.1,
    'ob_expiry': 120,
    'sl_buffer': 0.4,
    'entry_buffer': -0.2,
    'atr_multiplier': 8.5,
    'time_stop': 96
}
SYMBOL = 'BTC-USDT-SWAP'
# ==========================================

logger.info("📥 正在加载全局数据...")
loader = OKXDataLoader(symbol=SYMBOL, timeframe='1H')
df_raw = loader.fetch_data_by_date_range('2020-01-01', '2025-12-31')
df_global = add_smc_indicators(df_raw)
logger.info("✅ 数据加载完成，开始鲁棒性压力测试！\n")

# 定义我们要“微调”的邻域参数
test_cases = [
    {"name": "中心原参数 (Top 1)", "tweaks": {}},
    {"name": "寻找订单块变短", "tweaks": {"lookback": BASE_PARAMS['lookback'] - 2}},
    {"name": "寻找订单块变长", "tweaks": {"lookback": BASE_PARAMS['lookback'] + 2}},
    {"name": "订单块要求变严", "tweaks": {"atr_mult": BASE_PARAMS['atr_mult'] - 0.2}},
    {"name": "订单块要求变宽", "tweaks": {"atr_mult": BASE_PARAMS['atr_mult'] + 0.2}},
    {"name": "进场缓冲变得更深", "tweaks": {"entry_buffer": BASE_PARAMS['entry_buffer'] - 0.2}},
    {"name": "止损出场变得更紧", "tweaks": {"atr_multiplier": BASE_PARAMS['atr_multiplier'] - 1.0}}
]

results = []

for case in test_cases:
    # 组合当前测试的参数
    current_params = BASE_PARAMS.copy()
    current_params.update(case["tweaks"])

    logger.info(f"🔄 正在测试: [{case['name']}] ...")

    strategy = SMCStrategy(
        ema_period=current_params['ema_period'],
        lookback=current_params['lookback'],
        atr_mult=current_params['atr_mult'],
        ob_expiry=current_params['ob_expiry'],
        sl_buffer=current_params['sl_buffer'],
        entry_buffer=current_params['entry_buffer'],
        ai_config={'enabled': False}
    )

    df_signals = strategy.generate_signals(df_global.copy())

    trades = run_universal_backtest(
        df=df_signals,
        strategy_name="Robustness Test",
        symbol=SYMBOL,
        initial_capital=1000.0,
        max_risk=0.07,  # 用 7% 的暴利风控来测
        atr_multiplier=current_params['atr_multiplier'],
        fee_rate=0.0005,
        time_stop=current_params['time_stop'],
        out_logs=False  # 关闭每次回测的战报打印
    )

    if trades:
        df_res = pd.DataFrame(trades)
        net_pnl = df_res['pnl'].sum()
        win_rate = (df_res['pnl'] > 0).mean() * 100

        # 简单计算回撤
        equity = [1000.0]
        for pnl in df_res['pnl']: equity.append(equity[-1] + pnl)
        eq_s = pd.Series(equity)
        max_dd = ((eq_s.cummax() - eq_s) / eq_s.cummax()).max() * 100

        results.append({
            "测试项": case['name'],
            "净利润($)": round(net_pnl, 2),
            "胜率(%)": round(win_rate, 2),
            "最大回撤(%)": round(max_dd, 2),
            "交易次数": len(trades)
        })
    else:
        results.append({
            "测试项": case['name'], "净利润($)": 0, "胜率(%)": 0, "最大回撤(%)": 0, "交易次数": 0
        })

logger.info("\n" + "=" * 60)
logger.info("🛡️ 鲁棒性压力测试报告 (Robustness Report)")
logger.info("=" * 60)
df_report = pd.DataFrame(results)
logger.info(df_report.to_string(index=False))
logger.info("=" * 60)