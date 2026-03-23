#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/22/2026 12:24 AM
@File       : simple_momentum.py
@Description:
"""
import logging
import os
import sys

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(current_file))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pandas as pd

from config.loader import GLOBAL_SETTINGS
from src.data_feed.okx_loader import OKXDataLoader
from src.utils.log import get_logger
from src.data_process import create_range_bars_from_ohlc

logger = get_logger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==========================================
# ⚙️ 核心回测时间控制
# ==========================================
START_DATE = '2025-01-01'  # 建议先测最近几个月，5分钟数据量很大
END_DATE = '2025-12-31'

# ==========================================
# ⚙️ Bar类型选择
# ==========================================
USE_RANGE_BAR = True  # True: 使用Range Bar, False: 使用时间K线

# Range Bar 配置参数（仅在USE_RANGE_BAR=True时生效）
RANGE_BAR_CFG = {
    'tick_range': 150,  # Range Bar 的价格范围，150 ticks = 1.5U
    'tick_size': 0.01,  # 最小价格变动单位，ETH永续合约
    'max_bars': None,   # 最大生成的Bar数量，None表示无限制
}


def prepare_strategy_data(df: pd.DataFrame) -> pd.DataFrame:
    """计算策略所需的K线形态和ATR指标"""

    # 1. 基础K线属性
    df['bullish'] = df['close'] > df['open']
    df['bearish'] = df['close'] < df['open']
    df['body_pct'] = abs(df['close'] - df['open']) / df['open']

    # 2. 计算TR (True Range)
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = abs(df['high'] - df['prev_close'])
    df['tr3'] = abs(df['low'] - df['prev_close'])
    df['TR'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)

    # 3. 计算过去1小时平均ATR (5分钟K线，1小时=12根)
    df['ATR_1h'] = df['TR'].rolling(window=12).mean()

    # 4. 震荡判定：连续两根K线的TR小于过去一小时的平均ATR
    df['is_consolidation'] = (df['TR'] < df['ATR_1h']) & (df['TR'].shift(1) < df['ATR_1h'].shift(1))

    # 震荡发生后的有效窗口：假设形态只要在震荡发生后的紧接着4根K线内出现均可
    df['consolidation_recent'] = df['is_consolidation'].rolling(window=4).max() > 0

    # ================= 买入形态判定 =================
    # A: 连续3根阳线
    pat_long_A = df['bullish'] & df['bullish'].shift(1) & df['bullish'].shift(2)
    # B: 阳线 + 实体<0.1%的阴线 + 阳线
    pat_long_B = df['bullish'] & df['bearish'].shift(1) & (df['body_pct'].shift(1) < 0.001) & df['bullish'].shift(2)
    # C: 两根阳线 + 实体<0.1%的阴线
    pat_long_C = df['bearish'] & (df['body_pct'] < 0.001) & df['bullish'].shift(1) & df['bullish'].shift(2)

    df['Signal'] = 0
    # 满足震荡前提，且出现多头形态
    long_cond = df['consolidation_recent'].shift(1) & (pat_long_A | pat_long_B | pat_long_C)
    df.loc[long_cond, 'Signal'] = 1

    # ================= 做空形态判定 =================
    # A: 连续3根阴线
    pat_short_A = df['bearish'] & df['bearish'].shift(1) & df['bearish'].shift(2)
    # B: 阴线 + 实体<0.1%的阳线 + 阴线
    pat_short_B = df['bearish'] & df['bullish'].shift(1) & (df['body_pct'].shift(1) < 0.001) & df['bearish'].shift(2)
    # C: 两根阴线 + 实体<0.1%的阳线
    pat_short_C = df['bullish'] & (df['body_pct'] < 0.001) & df['bearish'].shift(1) & df['bearish'].shift(2)

    # 满足震荡前提，且出现空头形态
    short_cond = df['consolidation_recent'].shift(1) & (pat_short_A | pat_short_B | pat_short_C)
    df.loc[short_cond, 'Signal'] = -1

    return df


def run_backtest(df: pd.DataFrame, initial_capital=1000.0, fee_rate=0.0005):
    capital = initial_capital
    in_position = False
    position_type = 0
    entry_time = None
    entry_price = 0.0
    position_size_coin = 0.0
    trade_history = []

    logger.info(f"🚀 开始回测K线突破策略 (5分钟级别) | {df.index[0]} -> {df.index[-1]}")
    logger.info(f"初始资金: ${capital:.2f} | 交易手续费: {fee_rate * 100}%")

    # 逐K线迭代
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]
        idx = df.index[i]

        is_exiting = False
        exit_reason = ""

        if in_position:
            # ================= 离场逻辑 =================
            if position_type == 1:  # 持有多头
                # 条件1：连续两根阴线
                if row['bearish'] and prev_row['bearish']:
                    is_exiting = True
                    exit_reason = "连续2阴平多"
                # 条件2：一根阴线，且最低点比前一根阳线开盘价低
                elif row['bearish'] and prev_row['bullish'] and (row['low'] < prev_row['open']):
                    is_exiting = True
                    exit_reason = "阴破阳开平多"

            elif position_type == -1:  # 持有空头
                # 条件1：连续两根阳线
                if row['bullish'] and prev_row['bullish']:
                    is_exiting = True
                    exit_reason = "连续2阳平空"
                # 条件2：一根阳线，且最高点比前一根阴线开盘价高
                elif row['bullish'] and prev_row['bearish'] and (row['high'] > prev_row['open']):
                    is_exiting = True
                    exit_reason = "阳破阴开平空"

            if is_exiting:
                exit_price = row['close']
                # 计算盈亏 (这里采用全仓梭哈模式，因为没有固定止损位没法算风险暴露)
                gross_pnl = (exit_price - entry_price) * position_size_coin if position_type == 1 else (
                                                                                                               entry_price - exit_price) * position_size_coin

                entry_fee = position_size_coin * entry_price * fee_rate
                exit_fee = position_size_coin * exit_price * fee_rate
                total_fee = entry_fee + exit_fee
                net_pnl = gross_pnl - total_fee
                capital += net_pnl

                trade_history.append({
                    'entry_time': entry_time, 'exit_time': idx,
                    'type': 'LONG' if position_type == 1 else 'SHORT',
                    'entry': entry_price, 'exit': exit_price,
                    'pnl': net_pnl, 'capital': capital, 'reason': exit_reason
                })
                in_position = False

        # ================= 进场逻辑 =================
        if not in_position and row['Signal'] != 0:
            entry_time = idx
            entry_price = row['close']
            position_type = int(row['Signal'])

            # 使用1倍资金全仓参与（因为没有硬性止损价，全仓可控）
            position_size_coin = capital / entry_price
            in_position = True

    # ================= 打印报告 =================
    total_trades = len(trade_history)
    wins = [t for t in trade_history if t['pnl'] > 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    logger.info("\n" + "=" * 50)
    logger.info("📊 策略回测结果")
    logger.info("=" * 50)
    logger.info(f"总交易次数: {total_trades}")
    logger.info(f"胜率:      {win_rate * 100:.2f}%")
    logger.info(f"最终资金:   ${capital:.2f}")
    logger.info(f"总收益率:   {((capital - initial_capital) / initial_capital) * 100:.2f}%")

    if total_trades > 0:
        logger.info("\n🔍 最近5笔交易详情:")
        for t in trade_history[-5:]:
            logger.info(
                f"[{t['type']}] 进:{t['entry_time'].strftime('%m-%d %H:%M')} 出:{t['exit_time'].strftime('%m-%d %H:%M')} | 盈亏: ${t['pnl']:.2f} | 理由: {t['reason']}")


if __name__ == "__main__":
    # 强制设定为 5m 级别
    symbol = GLOBAL_SETTINGS.get("symbol", "ETH-USDT-SWAP")
    loader = OKXDataLoader(symbol=symbol, timeframe="5m")

    logger.info(f"正在拉取 {symbol} 5分钟K线数据...")
    df = loader.fetch_data_by_date_range(START_DATE, END_DATE)

    if df.empty:
        logger.error("数据拉取失败！请检查日期范围或网络。")
    else:
        # 根据配置选择Bar类型
        if USE_RANGE_BAR:
            # 使用Range Bar模式
            logger.info(f"使用Range Bar模式 (tick_range={RANGE_BAR_CFG['tick_range']}, tick_size={RANGE_BAR_CFG['tick_size']})...")
            range_bar_df = create_range_bars_from_ohlc(
                df=df,
                tick_range=RANGE_BAR_CFG['tick_range'],
                tick_size=RANGE_BAR_CFG['tick_size'],
                max_bars=RANGE_BAR_CFG['max_bars']
            )

            # 重命名列以兼容策略（期望的列名：open, high, low, close）
            # Range Bar生成器返回的列：open_px, high_px, low_px, close_px
            range_bar_df = range_bar_df.rename(columns={
                'open_px': 'open',
                'high_px': 'high',
                'low_px': 'low',
                'close_px': 'close'
            })

            # 添加volume列（总成交量 = 买入成交量 + 卖出成交量）
            if 'total_buy_vol' in range_bar_df.columns and 'total_sell_vol' in range_bar_df.columns:
                range_bar_df['volume'] = range_bar_df['total_buy_vol'] + range_bar_df['total_sell_vol']
            else:
                range_bar_df['volume'] = 0.0

            # 设置时间索引（使用open_ts转换为datetime）
            range_bar_df.index = pd.to_datetime(range_bar_df['open_ts'], unit='ns')

            if range_bar_df.empty:
                logger.error("Range Bar转换结果为空，请检查输入数据或参数配置！")
                sys.exit(1)

            backtest_df = range_bar_df
            bar_type = f"RangeBar {RANGE_BAR_CFG['tick_range']}ticks"

            logger.info(f"Range Bar转换完成：{len(backtest_df)} 个Bar")
            logger.info(f"Bar时间范围：{backtest_df.index[0]} 到 {backtest_df.index[-1]}")
            if 'tick_count' in backtest_df.columns:
                avg_ticks = backtest_df['tick_count'].mean()
                logger.info(f"平均每个Range Bar包含 {avg_ticks:.1f} 根原始K线")
        else:
            # 使用时间K线模式
            logger.info(f"使用时间K线模式 (timeframe=5m)...")
            backtest_df = df
            bar_type = "5m"
            logger.info(f"时间K线数据：{len(backtest_df)} 个Bar")
            logger.info(f"时间范围：{backtest_df.index[0]} 到 {backtest_df.index[-1]}")

        # 处理策略信号
        df = prepare_strategy_data(backtest_df)
        # 执行回测
        run_backtest(df, initial_capital=1000.0, fee_rate=0.0005)
