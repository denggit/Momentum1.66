#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import optuna
import pandas as pd
import warnings

from backtest.engine import run_universal_backtest
from config.loader import load_strategy_config
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy

warnings.filterwarnings('ignore')
logging.getLogger().setLevel(logging.ERROR)  # 关闭回测过程中的海量打印，只看 Optuna 进度

START_DATE = '2020-01-01'
END_DATE = '2025-12-31'
SMC_TIMEFRAME = '1H'
SYMBOL = 'BTC-USDT-SWAP'

# 加载基础配置 (用于保持那些不需要调的参数不变)
cfg = load_strategy_config("smc", SYMBOL)
engine_cfg = cfg.get("engine", {})

print(f"📥 正在加载全局数据 {SYMBOL} ({START_DATE} 至 {END_DATE})...")
loader = OKXDataLoader(symbol=SYMBOL, timeframe=SMC_TIMEFRAME)
df_raw = loader.fetch_data_by_date_range(START_DATE, END_DATE)
df_global = add_smc_indicators(df_raw)
print("✅ 数据加载完成，启动 Optuna 智能调参引擎！\n")


def objective(trial):
    # ==========================================
    # 🧠 Optuna 智能搜索空间 (参数范围)
    # ==========================================
    ema_period = trial.suggest_int('ema_period', 50, 200, step=10)
    lookback = trial.suggest_int('lookback', 10, 30, step=1)
    atr_mult = trial.suggest_float('atr_mult', 1.0, 3.0, step=0.1)
    ob_expiry = trial.suggest_int('ob_expiry', 24, 120, step=12)
    sl_buffer = trial.suggest_float('sl_buffer', 0.1, 1.0, step=0.1)
    entry_buffer = trial.suggest_float('entry_buffer', -0.5, 0.5, step=0.1)
    max_risk = trial.suggest_float('max_risk', 0.01, 0.1, step=0.01)
    time_stop = trial.suggest_int('time_stop', 24, 120, step=12)

    # 出场追踪止损宽松度
    atr_multiplier = trial.suggest_float('atr_multiplier', 3.0, 9.0, step=0.5)

    # ==========================================
    # ⚙️ 带着参数实例化策略并生成信号
    # ==========================================
    strategy = SMCStrategy(
        ema_period=ema_period,
        lookback=lookback,
        atr_mult=atr_mult,
        ob_expiry=ob_expiry,
        sl_buffer=sl_buffer,
        entry_buffer=entry_buffer,
        ai_config={'enabled': False}  # 纯调参阶段关闭 AI
    )

    # 注意：使用 copy() 防止污染全局数据
    df_signals = strategy.generate_signals(df_global.copy())

    # ==========================================
    # 🚀 呼叫你的全能引擎 (完美对齐 smc.py 参数)
    # ==========================================
    try:
        trades = run_universal_backtest(
            df=df_signals,
            strategy_name="Optuna Tuning",
            symbol=SYMBOL,
            initial_capital=engine_cfg.get("initial_capital", 1000.0),
            max_risk=max_risk,
            atr_multiplier=atr_multiplier,  # 这里用 Optuna 猜出的追踪止损值
            fee_rate=engine_cfg.get("fee_rate", 0.0005),
            time_stop=time_stop
        )
    except Exception as e:
        # 如果某组极端参数导致引擎报错，直接给这组参数打最低分
        return -9999.0

    # ==========================================
    # 🎯 核心评分机制 (Fitness Function)
    # ==========================================
    # 如果没产生足够的交易次数（比如少于30次），直接淘汰，防止过拟合
    if not trades or len(trades) < 30:
        return -9999.0

    df_res = pd.DataFrame(trades)
    net_pnl = df_res['Net_PnL'].sum()

    # 如果是亏钱的，淘汰
    if net_pnl <= 0:
        return -9999.0

    # 计算最大回撤 (基于 1000U 单利/复利累加估算)
    initial_cap = engine_cfg.get("initial_capital", 1000.0)
    equity = [initial_cap]
    for pnl in df_res['Net_PnL']:
        equity.append(equity[-1] + pnl)

    eq_s = pd.Series(equity)
    max_dd = ((eq_s.cummax() - eq_s) / eq_s.cummax()).max() * 100

    # 评分公式：Calmar Ratio (收益回撤比)
    calmar_ratio = (net_pnl / initial_cap) / (max_dd / 100) if max_dd > 0 else 0

    return calmar_ratio


if __name__ == "__main__":
    # 创建追求分数最大化的学习计划
    study = optuna.create_study(direction='maximize')

    print("🚀 开始暴力搜参，请耐心等待...")
    # 跑 100 轮测试 (视你的电脑配置，通常几分钟跑完)
    study.optimize(objective, n_trials=100, n_jobs=-1)

    print("\n" + "=" * 50)
    print("🏆 调参结束！宇宙最强 BTC SMC 参数组合：")
    print("=" * 50)
    for key, value in study.best_params.items():
        print(f"  {key}: {value}")
    print(f"\n📈 最佳评分 (Calmar Ratio): {study.best_value:.2f}")
    print("=" * 50)