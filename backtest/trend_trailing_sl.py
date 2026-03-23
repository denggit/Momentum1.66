#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/23/26 11:54 PM
@File       : trend_trailing_sl.py
@Description: 
"""
import logging
import os
import sys
import numpy as np
import pandas as pd

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(current_file))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_loader import OKXDataLoader
from src.utils.report import print_full_report
from src.utils.log import get_logger
from src.data_process import create_range_bars_from_ohlc

logger = get_logger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==========================================
# ⚙️ 全局配置参数 (直接在这里修改即可)
# ==========================================
START_DATE = '2025-01-01'
END_DATE = '2025-12-31'
TIMEFRAME = '5m'
SYMBOL = 'ETH-USDT-SWAP'

# ==========================================
# ⚙️ Bar类型选择
# ==========================================
USE_RANGE_BAR = False  # True: 使用Range Bar, False: 使用时间K线

# Range Bar 配置参数（仅在USE_RANGE_BAR=True时生效）
RANGE_BAR_CFG = {
    'tick_range': 2000,  # Range Bar 的价格范围，150 ticks = 1.5U
    'tick_size': 0.01,  # 最小价格变动单位，ETH永续合约
    'max_bars': None,   # 最大生成的Bar数量，None表示无限制
}

# 资金与风控引擎参数
ENGINE_CFG = {
    'initial_capital': 1000.0,
    'max_risk': 0.02,  # 单笔最大风险 (2% 本金)
    'fee_rate': 0.0005,  # 单边手续费率 (0.05%)
}

# 策略核心参数
STRAT_CFG = {
    'window': 5,  # 判定波段高低点所需的左右K线数
    'tick_size': 0.01,  # ETH合约最小变动单位 (1 tick)
    'tick_offset': 2,  # 止损放置在极值外侧的 tick 数量
}


# ==========================================


def run_trailing_trend_backtest(df: pd.DataFrame, strategy_name: str, initial_capital=1000.0, max_risk=0.02,
                                fee_rate=0.0005,
                                window=5, symbol=None):
    logger.info(f"🚀 启动自驱引擎: {strategy_name} | {df.index[0]} -> {df.index[-1]}")

    highs, lows = df['high'].values, df['low'].values
    tick_size = STRAT_CFG['tick_size']
    tick_offset = STRAT_CFG['tick_offset']

    # 预先计算波段高低点
    is_sh = np.zeros(len(df), dtype=bool)
    is_sl = np.zeros(len(df), dtype=bool)
    for i in range(window, len(df) - window):
        if highs[i] == np.max(highs[i - window:i + window + 1]): is_sh[i] = True
        if lows[i] == np.min(lows[i - window:i + window + 1]): is_sl[i] = True

    state = 'SEARCHING'
    capital = initial_capital
    trade_history = []

    swing_highs = []  # [(time, price), ...]
    swing_lows = []

    # 状态机与风控变量
    entry_target, sl_target = 0.0, 0.0
    target_high, target_low = 0.0, 0.0
    position_size, entry_executed_price, initial_risk_per_coin = 0.0, 0.0, 0.0
    entry_time = None
    trade_max_price, trade_min_price = 0.0, 0.0
    position_type = 0

    last_setup_long_time = None
    last_setup_short_time = None

    # 移动止损追踪变量
    last_sh_level = 0.0
    last_sl_level = 0.0
    breakout_triggered = False

    for i in range(window, len(df)):
        idx = df.index[i]
        row = df.iloc[i]

        # ==========================================
        # 1. 结构点更新与移动止损信号源
        # ==========================================
        check_idx = i - window
        if is_sh[check_idx]:
            swing_highs.append((df.index[check_idx], highs[check_idx]))
            # 只有在做多时，产生新的高点才意味着有了新的突破目标
            if state == 'IN_LONG':
                last_sh_level = highs[check_idx]
                breakout_triggered = False  # 重置突破标记，等待价格突破这个新高点

        if is_sl[check_idx]:
            swing_lows.append((df.index[check_idx], lows[check_idx]))
            # 只有在做空时，产生新的低点才意味着有了新的跌破目标
            if state == 'IN_SHORT':
                last_sl_level = lows[check_idx]
                breakout_triggered = False

        # 限制缓存大小防止内存溢出
        if len(swing_highs) > 20: swing_highs.pop(0)
        if len(swing_lows) > 20: swing_lows.pop(0)

        # ==========================================
        # 2. 持仓与移动止损逻辑
        # ==========================================
        if state in ['IN_LONG', 'IN_SHORT']:
            trade_max_price = max(trade_max_price, row['high'])
            trade_min_price = min(trade_min_price, row['low'])

            is_exiting, exit_price = False, 0.0

            if state == 'IN_LONG':
                # A. 检查止损（包含移动后的止损）
                if row['low'] <= sl_target:
                    exit_price, is_exiting = sl_target, True

                # B. 移动止损逻辑：如果价格突破了前一个波段高点
                elif not breakout_triggered and row['high'] > last_sh_level:
                    if len(swing_lows) > 0:
                        # 找到最近的一个回调波段低点
                        latest_low = swing_lows[-1][1]
                        new_sl = latest_low - (tick_offset * tick_size)
                        # 止损只能上移，不能下移
                        if new_sl > sl_target:
                            sl_target = new_sl
                            logger.info(
                                f"[{idx}] 📈 做多移动止损触发！价格突破 {last_sh_level:.2f}，止损上移至 {sl_target:.2f}")
                    breakout_triggered = True

            elif state == 'IN_SHORT':
                # A. 检查止损
                if row['high'] >= sl_target:
                    exit_price, is_exiting = sl_target, True

                # B. 移动止损逻辑：如果价格跌破了前一个波段低点
                elif not breakout_triggered and row['low'] < last_sl_level:
                    if len(swing_highs) > 0:
                        latest_high = swing_highs[-1][1]
                        new_sl = latest_high + (tick_offset * tick_size)
                        # 止损只能下移，不能上移
                        if new_sl < sl_target:
                            sl_target = new_sl
                            logger.info(
                                f"[{idx}] 📉 做空移动止损触发！价格跌破 {last_sl_level:.2f}，止损下移至 {sl_target:.2f}")
                    breakout_triggered = True

            if is_exiting:
                # 结算盈亏（因为没有止盈，所以退出理由一律是 Stop Loss 或 Trailing Stop）
                gross_pnl = (exit_price - entry_executed_price) * position_size * position_type
                accumulated_fee = position_size * entry_executed_price * fee_rate
                exit_fee = position_size * exit_price * fee_rate
                net_pnl = gross_pnl - (accumulated_fee + exit_fee)
                capital += net_pnl

                r_dist = initial_risk_per_coin if initial_risk_per_coin > 0 else 1e-5
                mfe = (trade_max_price - entry_executed_price) / r_dist if position_type == 1 else (
                                                                                                               entry_executed_price - trade_min_price) / r_dist
                mae = (entry_executed_price - trade_min_price) / r_dist if position_type == 1 else (
                                                                                                               trade_max_price - entry_executed_price) / r_dist

                exit_note = "Trailing SL" if (
                        (position_type == 1 and sl_target > entry_executed_price) or
                        (position_type == -1 and sl_target < entry_executed_price)
                ) else "Stop Loss"

                trade_history.append({
                    'entry_time': entry_time, 'exit_time': idx,
                    'type': 'LONG' if position_type == 1 else 'SHORT',
                    'entry': entry_executed_price, 'exit': exit_price, 'pnl': net_pnl,
                    'fee': accumulated_fee + exit_fee, 'capital': capital,
                    'mfe_r': round(mfe, 2), 'mae_r': round(mae, 2),
                    'sl_pct': round((r_dist / entry_executed_price) * 100, 4), 'note': exit_note
                })
                state = 'SEARCHING'
            continue

        # ==========================================
        # 3. 挂单入场逻辑
        # ==========================================
        if state == 'WAITING_LONG':
            if row['high'] > target_high or row['low'] < sl_target:
                # 如果没跌到前高就直接突破新高了，或者直接跌破了我们的止损底，挂单失效
                state = 'SEARCHING'
            elif row['low'] <= entry_target:
                entry_executed_price = entry_target
                initial_risk_per_coin = abs(entry_executed_price - sl_target)
                position_size = (capital * max_risk) / initial_risk_per_coin if initial_risk_per_coin > 0 else 0
                if position_size * entry_executed_price > capital: position_size = capital / entry_executed_price

                entry_time = idx
                trade_max_price, trade_min_price = entry_executed_price, entry_executed_price
                position_type = 1
                state = 'IN_LONG'
                last_sh_level = target_high  # 记录我们需要突破的高点
                breakout_triggered = False  # 准备移动止损
                logger.info(f"[{idx}] 🟢 做多入场: {entry_executed_price:.2f} | 初始止损: {sl_target:.2f}")
            continue

        elif state == 'WAITING_SHORT':
            if row['low'] < target_low or row['high'] > sl_target:
                state = 'SEARCHING'
            elif row['high'] >= entry_target:
                entry_executed_price = entry_target
                initial_risk_per_coin = abs(sl_target - entry_executed_price)
                position_size = (capital * max_risk) / initial_risk_per_coin if initial_risk_per_coin > 0 else 0
                if position_size * entry_executed_price > capital: position_size = capital / entry_executed_price

                entry_time = idx
                trade_max_price, trade_min_price = entry_executed_price, entry_executed_price
                position_type = -1
                state = 'IN_SHORT'
                last_sl_level = target_low
                breakout_triggered = False
                logger.info(f"[{idx}] 🔴 做空入场: {entry_executed_price:.2f} | 初始止损: {sl_target:.2f}")
            continue

        # ==========================================
        # 4. 寻找信号: Higher High 回踩 / Lower Low 回踩
        # ==========================================
        if state == 'SEARCHING':
            # ---------------- 做多逻辑 (Higher High) ----------------
            if len(swing_highs) >= 2 and len(swing_lows) >= 1:
                h1_time, h1 = swing_highs[-2]
                h2_time, h2 = swing_highs[-1]

                # 找到 H1 和 H2 之间形成的所有低点
                lows_between = [sl[1] for sl in swing_lows if h1_time < sl[0] < h2_time]

                # 条件: H2 是 Higher High，且不是刚刚检测过的配置，且两者间确实有波段低点
                if h2 > h1 and h2_time != last_setup_long_time and len(lows_between) > 0:
                    last_setup_long_time = h2_time
                    entry_target = h1  # 目标买点：前一个高点
                    # 初始止损：H1和H2之间最低的那个Low向下偏离2个Tick
                    sl_target = min(lows_between) - (tick_offset * tick_size)
                    target_high = h2

                    # 确保当前价格还在合理区间内 (没跌破结构)
                    if row['close'] > entry_target and sl_target < entry_target:
                        state = 'WAITING_LONG'

            # ---------------- 做空逻辑 (Lower Low) ----------------
            if len(swing_lows) >= 2 and len(swing_highs) >= 1:
                l1_time, l1 = swing_lows[-2]
                l2_time, l2 = swing_lows[-1]

                # 找到 L1 和 L2 之间形成的所有高点
                highs_between = [sh[1] for sh in swing_highs if l1_time < sh[0] < l2_time]

                if l2 < l1 and l2_time != last_setup_short_time and len(highs_between) > 0:
                    last_setup_short_time = l2_time
                    entry_target = l1  # 目标卖点：前一个低点
                    # 初始止损：L1和L2之间最高的高点向上偏离2个Tick
                    sl_target = max(highs_between) + (tick_offset * tick_size)
                    target_low = l2

                    if row['close'] < entry_target and sl_target > entry_target:
                        state = 'WAITING_SHORT'

    # ================= 触发打印报告 =================
    total_days = (df.index[-1] - df.index[0]).total_seconds() / (24 * 3600)
    try:
        print_full_report(trade_history, df, initial_capital, capital, strategy_name, total_days, ai_enabled=False,
                          symbol=symbol)
    except Exception as e:
        logger.error(f"调用系统报告打印失败: {e}")

    return trade_history


if __name__ == "__main__":
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=TIMEFRAME)
    logger.info(f"正在拉取 {SYMBOL} {TIMEFRAME} 历史数据...")
    df = loader.fetch_data_by_date_range(START_DATE, END_DATE)

    if df.empty:
        logger.error("拉取的数据为空，请检查网络或日期范围！")
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
            logger.info(f"使用时间K线模式 (timeframe={TIMEFRAME})...")
            backtest_df = df
            bar_type = f"{TIMEFRAME}"
            logger.info(f"时间K线数据：{len(backtest_df)} 个Bar")
            logger.info(f"时间范围：{backtest_df.index[0]} 到 {backtest_df.index[-1]}")

        run_trailing_trend_backtest(
            df=backtest_df,
            strategy_name=f"Trend Pullback & Trailing SL ({bar_type}) {SYMBOL}",
            symbol=SYMBOL,
            initial_capital=ENGINE_CFG['initial_capital'],
            max_risk=ENGINE_CFG['max_risk'],
            fee_rate=ENGINE_CFG['fee_rate'],
            window=STRAT_CFG['window']
        )