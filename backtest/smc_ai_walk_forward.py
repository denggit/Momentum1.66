import pandas as pd
import xgboost as xgb
import numpy as np
import os
from dateutil.relativedelta import relativedelta
from src.utils.log import get_logger
logger = get_logger(__name__)


# ==========================================
# 📊 绩效报告引擎 (百分比对齐版)
# ==========================================
def print_fair_report(trade_list, strategy_name, initial_cap=1000.0):
    if trade_list.empty:
        logger.info(f"⚠️ {strategy_name} 无交易记录。")
        return

    df_res = pd.DataFrame(trade_list)
    df_res['Entry_Time'] = pd.to_datetime(df_res['Entry_Time'])
    df_res = df_res.sort_values('Entry_Time')

    # --- 核心：在 1000U 本金上重新模拟复利 ---
    current_cap = initial_cap
    equity_curve = [initial_cap]

    # 模拟交易
    for _, row in df_res.iterrows():
        trade_pnl = current_cap * row['Ret_Pct']  # 使用还原后的百分比收益率
        current_cap += trade_pnl
        equity_curve.append(current_cap)

    logger.info("\n" + "=" * 70)
    logger.info(f" 📊 {strategy_name} - 深度量化绩效报告")
    logger.info("=" * 70)

    # 1. 逐年拆解
    logger.info("\n" + "=" * 70)
    logger.info(" 📅 逐年绩效拆解 (Yearly Breakdown)")
    logger.info("-" * 70)
    logger.info(f"{'年份':<6} | {'初始资金':<10} | {'净盈亏':<11} | {'当年收益率':<10} | {'胜率':<6} | {'最大回撤':<8}")
    logger.info("-" * 70)

    df_res['Year'] = df_res['Entry_Time'].dt.year
    temp_cap = initial_cap
    for year, group in df_res.groupby('Year'):
        y_equity = [temp_cap]
        for _, r in group.iterrows():
            y_equity.append(y_equity[-1] + (y_equity[-1] * r['Ret_Pct']))

        y_pnl = y_equity[-1] - temp_cap
        y_ret = (y_pnl / temp_cap) * 100
        y_win_rate = (group['Label'] == 1).mean() * 100

        # 回撤计算
        y_eq_s = pd.Series(y_equity)
        y_dd = (y_eq_s.cummax() - y_eq_s) / y_eq_s.cummax() * 100

        logger.info(
            f"{year:<6} | ${temp_cap:<10.2f} | ${y_pnl:<+10.2f} | {y_ret:>+9.2f}% | {y_win_rate:>5.1f}% | -{y_dd.max():>6.2f}%")
        temp_cap = y_equity[-1]

    # 2. 核心指标
    logger.info("\n" + "-" * 70)
    logger.info(" 📈 核心量化指标 (Core Metrics)")
    logger.info("-" * 70)
    total_trades = len(df_res)
    win_rate = (df_res['Label'] == 1).mean() * 100

    logger.info(f"总交易次数 (Total Trades):  {total_trades}")
    logger.info(f"胜率 (Win Rate):          {win_rate:.2f}%")
    logger.info(f"最终资金 (Final Cap):      ${current_cap:,.2f}")
    logger.info(f"总净收益率:               {((current_cap - initial_cap) / initial_cap * 100):.2f}%")
    logger.info("=" * 70 + "\n")


# ==========================================
# 🚀 还原百分比逻辑 & 滚动 AI
# ==========================================
def main():
    df = pd.read_csv('SMC_ML_Dataset.csv')
    df['Entry_Time'] = pd.to_datetime(df['Entry_Time'])
    df = df.sort_values('Entry_Time')

    # 1. 重要：还原每一笔交易的真实收益率 (%)
    # 假设 2020 年以 $1000 起步
    df['Original_Bal_Before'] = 1000.0 + df['Net_PnL'].cumsum().shift(1).fillna(0)
    df['Ret_Pct'] = df['Net_PnL'] / df['Original_Bal_Before']

    # 只取 2022 以后的数据进行测试
    df_eval = df[df['Entry_Time'] >= '2022-01-01'].copy()

    # 打印对照组 (无 AI)
    print_fair_report(df_eval, "SMC 裸跑 (无 AI - 对齐版)")

    # 2. 执行滚动 AI 拦截
    features = ['Hour', 'DayOfWeek', 'Dist_to_EMA', 'ADX', 'RSI', 'ATR_Rank', 'ATR_Slope', 'Body_Ratio', 'sl_pct']
    current_start = pd.to_datetime('2022-01-01')
    final_end = pd.to_datetime('2025-12-31')
    ai_threshold = 0.15
    ai_trades = []

    logger.info("🔄 正在进行季度滚动训练 (Walking Forward)...")
    while current_start <= final_end:
        train_start = current_start - relativedelta(years=4)
        test_end = current_start + relativedelta(months=3)

        df_train = df[(df['Entry_Time'] >= train_start) & (df['Entry_Time'] < current_start)].copy()
        df_test = df[(df['Entry_Time'] >= current_start) & (df['Entry_Time'] < test_end)].copy()

        if not df_test.empty:
            if len(df_train) >= 15:
                model = xgb.XGBClassifier(n_estimators=100, learning_rate=0.05, max_depth=3, random_state=42)
                model.fit(df_train[features], df_train['Label'])
                probs = model.predict_proba(df_test[features])[:, 1]
                df_test['Pass'] = (probs >= ai_threshold).astype(int)
                ai_trades.append(df_test[df_test['Pass'] == 1])
            else:
                ai_trades.append(df_test)
        current_start = test_end

    # 打印实验组 (滚动 AI)
    if ai_trades:
        print_fair_report(pd.concat(ai_trades), "SMC + AI 季度滚动版 (对齐版)")


if __name__ == "__main__":
    main()