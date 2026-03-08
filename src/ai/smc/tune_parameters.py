#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import os
import sys
import warnings

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import optuna
import pandas as pd

from backtest.engine import run_universal_backtest
from config.loader import load_strategy_config
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy
from src.utils.log import get_logger

logger = get_logger(__name__)

warnings.filterwarnings('ignore')
logging.getLogger().setLevel(logging.ERROR)  # 关闭回测过程中的海量打印，只看 Optuna 进度

START_DATE = '2020-01-01'
END_DATE = '2025-12-31'
SMC_TIMEFRAME = '1H'
SYMBOL = 'ETH-USDT-SWAP'

# 加载基础配置 (用于保持那些不需要调的参数不变)
cfg = load_strategy_config("smc", SYMBOL)
engine_cfg = cfg.get("engine", {})

logger.info(f"📥 正在加载全局数据 {SYMBOL} ({START_DATE} 至 {END_DATE})...")
loader = OKXDataLoader(symbol=SYMBOL, timeframe=SMC_TIMEFRAME)
df_raw = loader.fetch_data_by_date_range(START_DATE, END_DATE)
df_global = add_smc_indicators(df_raw)
logger.info("✅ 数据加载完成，启动 Optuna 智能调参引擎！\n")


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
    atr_multiplier = trial.suggest_float('atr_multiplier', 3.0, 9.0, step=0.5)
    time_stop = trial.suggest_int('time_stop', 24, 120, step=12)

    # 最大风险控制好
    max_risk = 0.07

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

    df_signals = strategy.generate_signals(df_global.copy())

    # ==========================================
    # 🚀 呼叫你的全能引擎
    # ==========================================
    try:
        trades = run_universal_backtest(
            df=df_signals,
            strategy_name="Optuna Tuning",
            symbol=SYMBOL,
            initial_capital=engine_cfg.get("initial_capital", 1000.0),
            max_risk=max_risk,
            atr_multiplier=atr_multiplier,
            fee_rate=engine_cfg.get("fee_rate", 0.0005),
            time_stop=time_stop,
            out_logs=False
        )
    except Exception as e:
        logger.error(f"❌ 引擎运行报错: {e}")
        return -9999.0

    # ==========================================
    # 🎯 核心评分机制 (Fitness Function)
    # ==========================================
    if not trades or len(trades) < 60:
        return -9999.0

    df_res = pd.DataFrame(trades)
    net_pnl = df_res['pnl'].sum()

    if net_pnl <= 0:
        return -9999.0

    # 计算胜率
    win_rate = (df_res['pnl'] > 0).mean() * 100

    # 计算最大回撤
    initial_cap = engine_cfg.get("initial_capital", 1000.0)
    equity = [initial_cap]
    for pnl in df_res['pnl']:
        equity.append(equity[-1] + pnl)

    eq_s = pd.Series(equity)
    max_dd = ((eq_s.cummax() - eq_s) / eq_s.cummax()).max() * 100

    # 计算年化收益率 (CAGR)
    total_years = 6.0  # 2020-2025 约为 6 年
    final_cap = equity[-1]
    cagr = ((final_cap / initial_cap) ** (1 / total_years) - 1) * 100  # 转换成百分比

    # 评分公式：标准卡玛比率 (年化收益 / 最大回撤)
    calmar_ratio = cagr / max_dd if max_dd > 0 else 0

    # 🌟 关键动作：把额外指标存进 Optuna 的记忆里
    trial.set_user_attr("CAGR(%)", cagr)
    trial.set_user_attr("Max_DD(%)", max_dd)
    trial.set_user_attr("Win_Rate(%)", win_rate)
    trial.set_user_attr("Trades", len(trades))
    trial.set_user_attr("Calmar_Ratio", calmar_ratio)

    return calmar_ratio


if __name__ == "__main__":
    study = optuna.create_study(direction='maximize')

    logger.info("🚀 开始暴力搜参，请耐心等待...")
    study.optimize(objective, n_trials=1000, n_jobs=-1)

    # ==========================================
    # 💾 终极收尾：将 Top 10 参数保存到 CSV
    # ==========================================
    logger.info("\n" + "=" * 50)
    logger.info("💾 正在将 Top 10 最强参数组合保存至 CSV...")

    # 获取所有成功跑完并没有被打 -9999 分的 trial
    complete_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE and t.value != -9999.0]

    # 按照评分 (Calmar Ratio) 从高到低排序
    complete_trials.sort(key=lambda t: t.value, reverse=True)

    # 截取前 100 名
    top_50 = complete_trials[:100]

    output_data = []
    for i, t in enumerate(top_50):
        # 提取我们在 objective 里塞进去的附加指标
        row_data = {
            "Rank": i + 1,
            "Calmar_Ratio": round(t.user_attrs.get("Calmar_Ratio"), 2),
            "CAGR(%)": round(t.user_attrs.get("CAGR(%)", 0), 2),
            "Max_DD(%)": round(t.user_attrs.get("Max_DD(%)", 0), 2),
            "Win_Rate(%)": round(t.user_attrs.get("Win_Rate(%)", 0), 2),
            "Trades": t.user_attrs.get("Trades", 0)
        }
        # 将参数列表也合并进这一行
        row_data.update(t.params)
        output_data.append(row_data)

    if output_data:
        df_top10 = pd.DataFrame(output_data)
        # 导出为 CSV 文件
        dir_name = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                                "data", "reports", "Optuna")
        os.makedirs(dir_name, exist_ok=True)
        csv_filename = os.path.join(dir_name, f"optuna_top10_params_{SYMBOL.split('-')[0]}.csv")

        df_top10.to_csv(csv_filename, index=False)
        logger.info(f"✅ 大功告成！Top 10 参数已成功保存至项目根目录: {csv_filename}")

        # 顺便在终端打印第一名瞻仰一下
        logger.info("=" * 50)
        logger.info("🏆 本次比赛第一名参数概览：")
        logger.info(df_top10.iloc[0].to_string())
        logger.info("=" * 50)
    else:
        logger.info("⚠️ 没找到有效结果，可能所有参数都亏损或没达到 30 笔交易要求。")
