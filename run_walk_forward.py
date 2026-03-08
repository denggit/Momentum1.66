#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/2/26 12:41 AM
@File       : run_walk_forward.py
@Description:
"""
import os
import sys

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(current_file)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pandas as pd
import xgboost as xgb
from dateutil.relativedelta import relativedelta
from src.utils.log import get_logger

logger = get_logger(__name__)


def run_walk_forward():
    logger.info("🚀 启动机构级滚动回测引擎 (Walk-Forward Engine)...")

    # 1. 加载原始纯净数据集 (确保这是不带 AI 拦截跑出来的原始信号)
    df = pd.read_csv('SMC_ML_Dataset.csv')
    df['Entry_Time'] = pd.to_datetime(df['Entry_Time'])

    # 2. 设定参数
    OOS_START = pd.to_datetime('2024-01-01')  # 实盘盲测开始时间
    END_DATE = pd.to_datetime('2025-12-31')  # 测试结束时间
    TRAIN_WINDOW_YEARS = 4  # 每次看过去 4 年的数据
    STEP_MONTHS = 6  # 每半年重训一次
    AI_THRESHOLD = 0.15  # AI 放行阈值

    features = ['Hour', 'DayOfWeek', 'Dist_to_EMA', 'ADX', 'RSI', 'ATR_Rank', 'ATR_Slope', 'Body_Ratio', 'sl_pct']

    current_test_start = OOS_START
    all_oos_results = []

    logger.info(f"📈 盲测范围: {OOS_START.date()} 至 {END_DATE.date()}")
    logger.info(f"⚙️  滚动配置: 窗口 {TRAIN_WINDOW_YEARS}年 | 步进 {STEP_MONTHS}个月 | 阈值 {AI_THRESHOLD}")
    logger.info("-" * 60)

    # 3. 滚动循环
    while current_test_start < END_DATE:
        # 计算当前轮次的起止时间
        train_start = current_test_start - relativedelta(years=TRAIN_WINDOW_YEARS)
        train_end = current_test_start
        test_end = current_test_start + relativedelta(months=STEP_MONTHS)

        # 截取训练集与测试集
        df_train = df[(df['Entry_Time'] >= train_start) & (df['Entry_Time'] < train_end)].copy()
        df_test = df[(df['Entry_Time'] >= current_test_start) & (df['Entry_Time'] < test_end)].copy()

        if len(df_train) < 20:
            logger.warning(f"⚠️ {current_test_start.date()} 轮次样本太少 ({len(df_train)})，跳过...")
            current_test_start = test_end
            continue

        # 训练当前版本的 AI
        model = xgb.XGBClassifier(
            n_estimators=100, learning_rate=0.05, max_depth=3,
            subsample=0.8, random_state=42, eval_metric='logloss'
        )
        model.fit(df_train[features], df_train['Label'])

        # 对未来半年进行“预测/拦截”
        if not df_test.empty:
            # 获取胜率概率
            probs = model.predict_proba(df_test[features])[:, 1]
            df_test['AI_Prob'] = probs
            df_test['AI_Pass'] = (df_test['AI_Prob'] >= AI_THRESHOLD).astype(int)
            all_oos_results.append(df_test)

            pass_count = df_test['AI_Pass'].sum()
            total_count = len(df_test)
            logger.info(
                f"✅ 阶段 [{current_test_start.date()} -> {test_end.date()}] | 训练样本: {len(df_train)} | 拦截率: {(1 - pass_count / total_count) * 100:.1f}%")

        # 时间轴推进
        current_test_start = test_end

    # 4. 汇总所有盲测（实盘模拟）结果
    final_oos_df = pd.concat(all_oos_results)

    # 5. 对比绩效
    raw_pnl = final_oos_df['Net_PnL'].sum()
    ai_pnl = final_oos_df[final_oos_df['AI_Pass'] == 1]['Net_PnL'].sum()

    raw_trades = len(final_oos_df)
    ai_trades = final_oos_df['AI_Pass'].sum()

    raw_win_rate = (final_oos_df['Label'] == 1).mean() * 100
    ai_win_rate = (final_oos_df[final_oos_df['AI_Pass'] == 1]['Label'] == 1).mean() * 100

    logger.info("\n" + "=" * 60)
    logger.info("🏆 终极滚动回测报告 (2024-2025 样本外盲测)")
    logger.info("=" * 60)
    logger.info(
        f"📊 交易频次: 裸跑 {raw_trades} 次 -> AI 过滤后 {ai_trades} 次 (拦截了 {raw_trades - ai_trades} 笔垃圾单)")
    logger.info(f"🎯 综合胜率: 裸跑 {raw_win_rate:.2f}% -> AI 过滤后 {ai_win_rate:.2f}%")
    logger.info(f"💰 累计利润: 裸跑 ${raw_pnl:.2f} -> AI 过滤后 ${ai_pnl:.2f}")

    improvement = ((ai_pnl - raw_pnl) / abs(raw_pnl)) * 100 if raw_pnl != 0 else 0
    logger.info(f"📈 AI 带来的利润增幅: {improvement:.2f}%")

    # 检查 2024 年的表现 (最考验风控的一年)
    df_2024 = final_oos_df[final_oos_df['Entry_Time'].dt.year == 2024]
    raw_2024 = df_2024['Net_PnL'].sum()
    ai_2024 = df_2024[df_2024['AI_Pass'] == 1]['Net_PnL'].sum()
    logger.info(f"🛡️  2024 地狱年度表现: 裸跑 ${raw_2024:.2f} -> AI 拦截后 ${ai_2024:.2f}")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_walk_forward()
