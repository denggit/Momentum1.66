#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import pandas as pd
import pandas_ta as ta
import sys

# 确保能导入 src 目录下的模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.data_feed.okx_loader import OKXDataLoader


def build_ml_dataset(trade_log_path: str, symbol: str, timeframe: str, start_date: str, end_date: str):
    print(f"🚀 正在为 {symbol} 构建 AI 训练集...")

    # 1. 加载交易日志 (你的错题本)
    if not os.path.exists(trade_log_path):
        raise FileNotFoundError(f"找不到交易日志: {trade_log_path}，请确认路径是否正确！")

    df_trades = pd.read_csv(trade_log_path)
    df_trades['Entry_Time'] = pd.to_datetime(df_trades['Entry_Time'])

    # 【核心：生成标签 Y】净利润大于0标记为1 (真突破)，否则标记为0 (假突破)
    df_trades['Label'] = (df_trades['Net_PnL'] > 0).astype(int)

    # 2. 拉取全量 K 线数据 (考卷源文件)
    print(f"📥 正在从本地/OKX拉取 {symbol} 的 K 线数据...")
    loader = OKXDataLoader(symbol=symbol, timeframe=timeframe)
    df_klines = loader.fetch_data_by_date_range(start_date, end_date)

    if df_klines.empty:
        raise ValueError("K线数据拉取失败，请检查时间范围或网络！")

    print("🧠 正在计算高维 AI 特征 (Feature Engineering)...")

    # ==========================================
    # 构造 AI 专属特征向量 (X)
    # ==========================================
    # 维度 1: 趋势与偏离度
    df_klines['EMA_144'] = ta.ema(df_klines['close'], length=144)
    # 计算当前价格距离均线的乖离率 (百分比)
    df_klines['Dist_to_EMA'] = (df_klines['close'] - df_klines['EMA_144']) / df_klines['EMA_144'] * 100

    # 维度 2: 动能指标
    adx_df = ta.adx(df_klines['high'], df_klines['low'], df_klines['close'], length=14)
    df_klines['ADX'] = adx_df['ADX_14'] if adx_df is not None else 0
    df_klines['RSI'] = ta.rsi(df_klines['close'], length=14)

    # 维度 3: 波动率微观结构
    df_klines['ATR'] = ta.atr(df_klines['high'], df_klines['low'], df_klines['close'], length=14)
    df_klines['ATR_Rank'] = df_klines['ATR'].rolling(window=240).rank(pct=True)
    # ATR 斜率：过去 3 根 K 线的变化率，判断波动率是在扩张还是缩水
    df_klines['ATR_Slope'] = df_klines['ATR'].pct_change(periods=3)

    # 维度 4: K 线形态学 (当前K线实体的强弱比例)
    df_klines['Body_Ratio'] = abs(df_klines['close'] - df_klines['open']) / (
                df_klines['high'] - df_klines['low'] + 1e-8)

    # 维度 5: 时空特征
    df_klines['Hour'] = df_klines.index.hour
    df_klines['DayOfWeek'] = df_klines.index.dayofweek

    # 剔除因为计算均线和指标产生的头部空数据
    df_klines.dropna(inplace=True)

    # 3. 拼图：将 K 线特征“左连接”到交易日志中
    print("🔗 正在对齐交易时间轴与 K 线特征...")
    # 去除 K 线索引的时区以便与 TradeLog 对齐
    df_klines.index = df_klines.index.tz_localize(None)

    dataset = pd.merge(
        df_trades,
        df_klines,
        left_on='Entry_Time',
        right_index=True,
        how='inner'
    )

    # 4. 筛选最终给 AI 吃的纯净列
    feature_cols = [
        'Entry_Time', 'Type', 'Net_PnL', 'Label',  # 交易基础信息与目标 Y
        'Hour', 'DayOfWeek',  # X1, X2: 时空特征
        'Dist_to_EMA', 'ADX', 'RSI',  # X3, X4, X5: 动能偏离特征
        'ATR_Rank', 'ATR_Slope', 'Body_Ratio',  # X6, X7, X8: 波动与形态特征
        'sl_pct'
    ]

    df_final = dataset[feature_cols].copy()

    # 保存数据集
    output_path = os.path.join(os.path.dirname(trade_log_path), f'SMC_ML_Dataset_{symbol}.csv')
    df_final.to_csv(output_path, index=False)

    print("\n" + "=" * 50)
    print(f"✅ AI 训练集构建成功！共 {len(df_final)} 条有效样本。")
    print(f"📁 文件已保存至: {output_path}")

    # 打印正负样本比例（极度重要，关乎模型能否学到东西）
    win_rate = df_final['Label'].mean() * 100
    count_0 = len(df_final[df_final['Label'] == 0])
    count_1 = len(df_final[df_final['Label'] == 1])
    print(f"⚖️ 样本分布: 假突破(0) 有 {count_0} 笔 | 真突破(1) 有 {count_1} 笔 (原始胜率 {win_rate:.1f}%)")
    print("=" * 50)


if __name__ == "__main__":
    # 请确保这个路径指向你刚刚跑出来的那个 1H SMC 纯净版的 TradeLog.csv
    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                            "data", "reports", "SMC",
                            "SMC_聪明钱波段猎手_(1H_Order_Block)_2020-01-11_2025-12-31_False.csv")
    # 执行构建
    build_ml_dataset(
        trade_log_path=log_path,
        symbol="ETH-USDT-SWAP",
        timeframe="1H",
        start_date="2020-01-01",
        end_date="2025-12-31"
    )