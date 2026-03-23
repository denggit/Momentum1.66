#!/usr/bin/env python
# -*- coding: utf-8 -*-
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
# 1. 回测基础信息
START_DATE = '2025-01-01'
END_DATE = '2025-12-31'
TIMEFRAME = '5m'  # 原始时间K线，用于加载数据
SYMBOL = 'ETH-USDT-SWAP'

# 2. 资金与风控引擎参数
ENGINE_CFG = {
    'initial_capital': 1000.0,  # 初始资金
    'max_risk': 0.02,  # 单笔最大风险 (2% 本金)
    'fee_rate': 0.0005,  # 单边手续费率 (0.05%)
}

# 3. Bar类型选择
USE_RANGE_BAR = True  # True: 使用Range Bar, False: 使用时间K线

# 4. Range Bar 配置参数（仅在USE_RANGE_BAR=True时生效）
RANGE_BAR_CFG = {
    'tick_range': 150,  # Range Bar 的价格范围，150 ticks = 1.5U
    'tick_size': 0.01,  # 最小价格变动单位，ETH永续合约
    'max_bars': None,   # 最大生成的Bar数量，None表示无限制
}

# 5. 策略核心参数
STRAT_CFG = {
    'window': 5,  # 判定波段高低点所需的左右K线数 (5意味着左5根右5根)
    'sl_buffer': 1.0,  # 止损价距离前一个极值的缓冲距离 (1U)
}


# ==========================================


def run_choch_backtest(df: pd.DataFrame, strategy_name: str, initial_capital=1000.0, max_risk=0.02, fee_rate=0.0005,
                       window=5, sl_buffer=1.0, symbol=None):
    """
    SMC 结构破坏 (CHOCH) 策略自驱回测引擎
    """
    logger.info(f"🚀 启动自驱引擎: {strategy_name} | {df.index[0]} -> {df.index[-1]}")

    highs, lows = df['high'].values, df['low'].values

    # 预存波段高低点
    is_sh = np.zeros(len(df), dtype=bool)
    is_sl = np.zeros(len(df), dtype=bool)
    for i in range(window, len(df) - window):
        if highs[i] == np.max(highs[i - window:i + window + 1]): is_sh[i] = True
        if lows[i] == np.min(lows[i - window:i + window + 1]): is_sl[i] = True

    state = 'SEARCHING'
    capital = initial_capital
    trade_history = []
    swing_highs, swing_lows = [], []

    entry_target, sl_target, tp_target = 0.0, 0.0, 0.0
    position_size, entry_executed_price, initial_risk_per_coin = 0.0, 0.0, 0.0
    entry_time = None
    trade_max_price, trade_min_price = 0.0, 0.0
    position_type = 0  # 1 为多头, -1 为空头

    for i in range(window, len(df)):
        idx = df.index[i]
        row = df.iloc[i]

        # ==========================================
        # 1. 持仓追踪与退出逻辑
        # ==========================================
        if state in ['IN_LONG', 'IN_SHORT']:
            # 更新本单生命周期内的极值，用于生成 MFE/MAE
            trade_max_price = max(trade_max_price, row['high'])
            trade_min_price = min(trade_min_price, row['low'])

            is_exiting, exit_price, exit_note = False, 0.0, ""

            if state == 'IN_LONG':
                if row['low'] <= sl_target:
                    exit_price, is_exiting, exit_note = sl_target, True, "Stop Loss"
                elif row['high'] >= tp_target:
                    exit_price, is_exiting, exit_note = tp_target, True, "Take Profit"

            elif state == 'IN_SHORT':
                if row['high'] >= sl_target:
                    exit_price, is_exiting, exit_note = sl_target, True, "Stop Loss"
                elif row['low'] <= tp_target:
                    exit_price, is_exiting, exit_note = tp_target, True, "Take Profit"

            if is_exiting:
                # 结算盈亏
                gross_pnl = (exit_price - entry_executed_price) * position_size * position_type
                accumulated_fee = position_size * entry_executed_price * fee_rate
                exit_fee = position_size * exit_price * fee_rate
                net_pnl = gross_pnl - (accumulated_fee + exit_fee)
                capital += net_pnl

                # 计算给报告展示的 R 乘数与 MAE/MFE
                r_dist = initial_risk_per_coin if initial_risk_per_coin > 0 else 1e-5
                mfe = (trade_max_price - entry_executed_price) / r_dist if position_type == 1 else (
                                                                                                               entry_executed_price - trade_min_price) / r_dist
                mae = (entry_executed_price - trade_min_price) / r_dist if position_type == 1 else (
                                                                                                               trade_max_price - entry_executed_price) / r_dist

                # 兼容 report.py 的输出字典结构
                trade_history.append({
                    'entry_time': entry_time, 'exit_time': idx,
                    'type': 'LONG' if position_type == 1 else 'SHORT',
                    'entry': entry_executed_price, 'exit': exit_price, 'pnl': net_pnl,
                    'fee': accumulated_fee + exit_fee,
                    'capital': capital, 'mfe_r': round(mfe, 2), 'mae_r': round(mae, 2),
                    'sl_pct': round((r_dist / entry_executed_price) * 100, 4), 'note': exit_note
                })

                state = 'SEARCHING'
                swing_highs.clear()
                swing_lows.clear()
            continue

        # ==========================================
        # 2. 更新过去的高低点结构
        # ==========================================
        check_idx = i - window
        if is_sh[check_idx]:
            swing_highs.append((df.index[check_idx], highs[check_idx]))
            if len(swing_highs) > 10: swing_highs.pop(0)
        if is_sl[check_idx]:
            swing_lows.append((df.index[check_idx], lows[check_idx]))
            if len(swing_lows) > 10: swing_lows.pop(0)

        # ==========================================
        # 3. 挂单入场逻辑
        # ==========================================
        if state == 'WAITING_LONG':
            if row['low'] <= entry_target:
                entry_executed_price = entry_target
                initial_risk_per_coin = abs(entry_executed_price - sl_target)

                # 使用最大风险控制仓位，并封顶全仓
                position_size = (capital * max_risk) / initial_risk_per_coin if initial_risk_per_coin > 0 else 0
                if position_size * entry_executed_price > capital: position_size = capital / entry_executed_price

                entry_time = idx
                trade_max_price, trade_min_price = entry_executed_price, entry_executed_price
                position_type = 1
                state = 'IN_LONG'
            elif row['low'] <= sl_target or row['high'] >= tp_target:
                # 挂单时被止损或止盈提前击穿，说明剧本失效，取消挂单重新寻找结构
                state = 'SEARCHING'
            continue

        elif state == 'WAITING_SHORT':
            if row['high'] >= entry_target:
                entry_executed_price = entry_target
                initial_risk_per_coin = abs(sl_target - entry_executed_price)

                position_size = (capital * max_risk) / initial_risk_per_coin if initial_risk_per_coin > 0 else 0
                if position_size * entry_executed_price > capital: position_size = capital / entry_executed_price

                entry_time = idx
                trade_max_price, trade_min_price = entry_executed_price, entry_executed_price
                position_type = -1
                state = 'IN_SHORT'
            elif row['high'] >= sl_target or row['low'] <= tp_target:
                state = 'SEARCHING'
            continue

        # ==========================================
        # 4. 寻找 CHOCH 信号 (趋势破坏)
        # ==========================================
        if state == 'SEARCHING':
            # ---------------- 做多逻辑 ----------------
            if len(swing_highs) >= 4 and len(swing_lows) >= 3:
                sh_prev = [p[1] for p in swing_highs[-4:-1]]
                sl_prev = [p[1] for p in swing_lows[-3:]]
                sh_latest = swing_highs[-1][1]

                # 确认下跌趋势：3个 Lower High 和 3个 Lower Low
                is_downtrend = (sh_prev[0] > sh_prev[1] > sh_prev[2]) and (sl_prev[0] > sl_prev[1] > sl_prev[2])

                # 结构破坏：最新高点突破了上一个 Lower High
                if is_downtrend and sh_latest > sh_prev[-1]:
                    entry_target = sl_prev[1]  # 前前一个Low
                    sl_target = sl_prev[2] - sl_buffer  # 最低点向下缓冲
                    tp_target = sh_latest  # 刚打出的 Higher High

                    if sl_target < entry_target:
                        state = 'WAITING_LONG'
                        swing_highs.clear()
                        swing_lows.clear()
                        continue

            # ---------------- 做空逻辑 ----------------
            if len(swing_highs) >= 3 and len(swing_lows) >= 4:
                sh_prev_s = [p[1] for p in swing_highs[-3:]]
                sl_prev_s = [p[1] for p in swing_lows[-4:-1]]
                sl_latest = swing_lows[-1][1]

                # 确认上涨趋势：3个 Higher High 和 3个 Higher Low
                is_uptrend = (sh_prev_s[0] < sh_prev_s[1] < sh_prev_s[2]) and (
                            sl_prev_s[0] < sl_prev_s[1] < sl_prev_s[2])

                # 结构破坏：最新低点跌破了上一个 Higher Low
                if is_uptrend and sl_latest < sl_prev_s[-1]:
                    entry_target = sh_prev_s[1]  # 前前一个High
                    sl_target = sh_prev_s[2] + sl_buffer  # 最高点向上缓冲
                    tp_target = sl_latest  # 刚打出的 Lower Low

                    if sl_target > entry_target:
                        state = 'WAITING_SHORT'
                        swing_highs.clear()
                        swing_lows.clear()
                        continue

    # ================= 触发打印报告 =================
    total_days = (df.index[-1] - df.index[0]).total_seconds() / (24 * 3600)
    try:
        print_full_report(trade_history, df, initial_capital, capital, strategy_name, total_days, ai_enabled=False,
                          symbol=symbol)
    except Exception as e:
        logger.error(f"调用系统报告打印失败: {e}，请确认 src.utils.report 组件可用。")

    return trade_history


if __name__ == "__main__":
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=TIMEFRAME)
    logger.info(f"正在拉取 {SYMBOL} {TIMEFRAME} 历史数据...")
    df = loader.fetch_data_by_date_range(START_DATE, END_DATE)

    if df.empty:
        logger.error("拉取的数据为空，请检查网络或日期范围！")
    else:
        if USE_RANGE_BAR:
            # 使用Range Bar
            logger.info(f"使用Range Bar模式 (tick_range={RANGE_BAR_CFG['tick_range']}, tick_size={RANGE_BAR_CFG['tick_size']})...")
            range_bar_df = create_range_bars_from_ohlc(
                df=df,
                tick_range=RANGE_BAR_CFG['tick_range'],
                tick_size=RANGE_BAR_CFG['tick_size'],
                max_bars=RANGE_BAR_CFG['max_bars']
            )

            # 重命名列以兼容CHOCH策略（期望的列名：open, high, low, close）
            # Range Bar生成器返回的列：open_px, high_px, low_px, close_px
            range_bar_df = range_bar_df.rename(columns={
                'open_px': 'open',
                'high_px': 'high',
                'low_px': 'low',
                'close_px': 'close'
            })

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
            # 使用时间K线
            logger.info(f"使用时间K线模式 (timeframe={TIMEFRAME})...")
            backtest_df = df
            bar_type = f"{TIMEFRAME}"
            logger.info(f"时间K线数据：{len(backtest_df)} 个Bar")
            logger.info(f"时间范围：{backtest_df.index[0]} 到 {backtest_df.index[-1]}")

        # 直接使用头部写死的配置参数
        run_choch_backtest(
            df=backtest_df,
            strategy_name=f"CHOCH ({bar_type} Structure Break) {SYMBOL}",
            symbol=SYMBOL,
            initial_capital=ENGINE_CFG['initial_capital'],
            max_risk=ENGINE_CFG['max_risk'],
            fee_rate=ENGINE_CFG['fee_rate'],
            window=STRAT_CFG['window'],
            sl_buffer=STRAT_CFG['sl_buffer']
        )