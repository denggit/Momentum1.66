#!/usr/bin/env python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from src.utils.report import print_full_report  # 【新增】导入报告专家


def run_universal_backtest(df: pd.DataFrame, strategy_name: str, initial_capital=1000.0, max_risk=0.02,
                           atr_multiplier=4.5, fee_rate=0.0005, target_r=None, reverse_cooldown=24):
    capital = initial_capital
    in_position = False
    position_type, entry_time, entry_price, stop_loss = 0, None, 0.0, 0.0
    position_size_coin, accumulated_fee, initial_risk_per_coin = 0.0, 0.0, 0.0
    trade_history = []
    trade_max_price, trade_min_price = 0.0, 0.0
    last_win_long_time, last_win_short_time = None, None

    start_time = df.index[0]
    total_days = (df.index[-1] - df.index[0]).total_seconds() / (24 * 3600)
    print(f"\n=== 🚀 启动 {strategy_name} | 反向冷却: {reverse_cooldown}h ===")

    for index, row in df.iterrows():
        if in_position:
            trade_max_price = max(trade_max_price, row['high'])
            trade_min_price = min(trade_min_price, row['low'])

            # 离场逻辑
            exit_price, is_exiting = 0.0, False
            if position_type == 1:
                if row['low'] <= stop_loss:
                    exit_price, is_exiting = stop_loss, True
                else:
                    stop_loss = max(stop_loss, row['close'] - (row['ATR'] * atr_multiplier))
            else:
                if row['high'] >= stop_loss:
                    exit_price, is_exiting = stop_loss, True
                else:
                    stop_loss = min(stop_loss, row['close'] + (row['ATR'] * atr_multiplier))

            if is_exiting:
                net_pnl = (exit_price - entry_price) * position_size_coin * position_type - (
                            accumulated_fee + position_size_coin * exit_price * fee_rate)
                capital += net_pnl
                if net_pnl > 0:
                    if position_type == 1:
                        last_win_long_time = index
                    else:
                        last_win_short_time = index

                # 计算 MFE/MAE
                r = initial_risk_per_coin
                mfe = (trade_max_price - entry_price) / r if position_type == 1 else (entry_price - trade_min_price) / r
                mae = (entry_price - trade_min_price) / r if position_type == 1 else (trade_max_price - entry_price) / r

                trade_history.append({
                    'entry_time': entry_time, 'exit_time': index, 'type': 'LONG' if position_type == 1 else 'SHORT',
                    'entry': entry_price, 'exit': exit_price, 'pnl': net_pnl,
                    'fee': accumulated_fee + position_size_coin * exit_price * fee_rate,
                    'capital': capital, 'mfe_r': round(mfe, 2), 'mae_r': round(mae, 2)
                })
                in_position = False

        # 进场逻辑
        if row['Signal'] != 0 and not in_position:
            # 12h 冷却拦截
            if reverse_cooldown > 0:
                if row['Signal'] == 1 and last_win_short_time and (
                        index - last_win_short_time).total_seconds() / 3600 <= reverse_cooldown: continue
                if row['Signal'] == -1 and last_win_long_time and (
                        index - last_win_long_time).total_seconds() / 3600 <= reverse_cooldown: continue

            entry_price, position_type = row['close'], row['Signal']
            stop_loss = row['SL_Price'] if 'SL_Price' in df.columns and not pd.isna(row['SL_Price']) else (
                        entry_price - row['ATR'] * atr_multiplier * position_type)
            initial_risk_per_coin = abs(entry_price - stop_loss)

            if initial_risk_per_coin > 0:
                position_size_coin = (capital * max_risk) / initial_risk_per_coin
                in_position, entry_time = True, index
                accumulated_fee = position_size_coin * entry_price * fee_rate
                trade_max_price = trade_min_price = entry_price

    # 最后调用专家打印报告
    print_full_report(trade_history, df, initial_capital, capital, strategy_name, total_days)