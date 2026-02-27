#!/usr/bin/env python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd


def run_universal_backtest(df: pd.DataFrame, strategy_name: str, initial_capital=1000.0, max_risk=0.02,
                           atr_multiplier=4.5, fee_rate=0.0005, target_r=None):
    capital = initial_capital
    in_position = False
    position_type = 0
    entry_time = None
    entry_price = 0.0
    stop_loss = 0.0
    position_size_coin = 0.0
    accumulated_fee = 0.0
    initial_risk_per_coin = 0.0
    trade_history = []

    start_time_str = df.index[0].strftime('%Y-%m-%d')
    end_time_str = df.index[-1].strftime('%Y-%m-%d')
    total_days = (df.index[-1] - df.index[0]).total_seconds() / (24 * 3600)

    print(f"\n=== 🚀 启动 {strategy_name} 引擎测试 | {start_time_str} 至 {end_time_str} ({total_days:.1f} 天) ===")

    tp_str = f"{target_r}R" if target_r else "无(纯追踪)"
    print(f"初始资金: ${capital} | 单笔风控: {max_risk * 100}% | ATR止损: {atr_multiplier}x | 强制止盈: {tp_str}")

    for index, row in df.iterrows():
        just_closed = False

        # ==========================================
        # 1. 离场逻辑 (加入了目标止盈)
        # ==========================================
        if in_position:
            exit_price = 0.0
            is_exiting = False

            # 计算当前的 R乘数 (盈亏比)
            open_profit = (row['close'] - entry_price) if position_type == 1 else (entry_price - row['close'])
            current_r = open_profit / initial_risk_per_coin if initial_risk_per_coin > 0 else 0

            # 【核心】：如果设置了目标 R，且当前浮盈达标，立刻强制市价止盈！
            if target_r is not None and current_r >= target_r:
                exit_price = row['close']
                is_exiting = True
            else:
                # 否则继续普通的追踪止损
                if position_type == 1:
                    if row['low'] <= stop_loss:
                        exit_price = stop_loss
                        is_exiting = True
                    else:
                        trailing_sl = row['close'] - (row['ATR'] * atr_multiplier)
                        if trailing_sl > stop_loss: stop_loss = trailing_sl
                elif position_type == -1:
                    if row['high'] >= stop_loss:
                        exit_price = stop_loss
                        is_exiting = True
                    else:
                        trailing_sl = row['close'] + (row['ATR'] * atr_multiplier)
                        if trailing_sl < stop_loss: stop_loss = trailing_sl

            if is_exiting:
                exit_fee = position_size_coin * exit_price * fee_rate
                total_trade_fee = accumulated_fee + exit_fee
                gross_pnl = (exit_price - entry_price) * position_size_coin if position_type == 1 else (
                                                                                                                   entry_price - exit_price) * position_size_coin
                net_pnl = gross_pnl - total_trade_fee
                capital += net_pnl

                trade_history.append({
                    'entry_time': entry_time, 'exit_time': index,
                    'type': 'LONG' if position_type == 1 else 'SHORT',
                    'entry': entry_price, 'exit': exit_price,
                    'pnl': net_pnl, 'fee': total_trade_fee, 'capital': capital
                })
                in_position = False
                just_closed = True

        # ==========================================
        # 2. 进场逻辑
        # ==========================================
        if row['Signal'] != 0 and not in_position:
            entry_time = index
            entry_price = row['close']
            atr_value = row['ATR']
            risk_amount_usdt = capital * max_risk

            if row['Signal'] == 1:
                position_type = 1
                stop_loss = entry_price - (atr_value * atr_multiplier)
                sl_distance = entry_price - stop_loss
            elif row['Signal'] == -1:
                position_type = -1
                stop_loss = entry_price + (atr_value * atr_multiplier)
                sl_distance = stop_loss - entry_price

            if sl_distance > 0:
                position_size_coin = risk_amount_usdt / sl_distance
                if (position_size_coin * entry_price / capital) > 10:
                    position_size_coin = (capital * 10) / entry_price
                in_position = True
                initial_risk_per_coin = sl_distance
                accumulated_fee = position_size_coin * entry_price * fee_rate

    # 期末强平
    if in_position:
        last_time = df.index[-1]
        last_close = df.iloc[-1]['close']
        exit_fee = position_size_coin * last_close * fee_rate
        total_trade_fee = accumulated_fee + exit_fee
        gross_pnl = (last_close - entry_price) * position_size_coin if position_type == 1 else (
                                                                                                           entry_price - last_close) * position_size_coin
        net_pnl = gross_pnl - total_trade_fee
        capital += net_pnl
        trade_history.append(
            {'entry_time': entry_time, 'exit_time': last_time, 'type': 'LONG' if position_type == 1 else 'SHORT',
             'entry': entry_price, 'exit': last_close, 'pnl': net_pnl, 'fee': total_trade_fee, 'capital': capital})

    # ========================== 战报输出 ==========================
    total_trades = len(trade_history)
    if total_trades == 0:
        print("没有产生任何交易。")
        return

    win_trades = sum(1 for t in trade_history if t['pnl'] > 0)
    gross_profit = sum(t['pnl'] for t in trade_history if t['pnl'] > 0)
    gross_loss = sum(abs(t['pnl']) for t in trade_history if t['pnl'] <= 0)

    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    for t in trade_history:
        if t['capital'] > peak_capital: peak_capital = t['capital']
        drawdown = (peak_capital - t['capital']) / peak_capital
        if drawdown > max_drawdown_pct: max_drawdown_pct = drawdown

    win_rate = win_trades / total_trades
    years = total_days / 365.25
    cagr = ((capital / initial_capital) ** (1 / years) - 1) if years > 0 and capital > 0 else 0
    pnl_ratio = (gross_profit / win_trades) / (gross_loss / (total_trades - win_trades)) if (
                                                                                                        total_trades - win_trades) > 0 and win_trades > 0 else 0

    print(f"总交易次数: {total_trades} | 胜率: {win_rate * 100:.2f}% | 盈亏比: {pnl_ratio:.2f}")
    print(f"最大回撤: {max_drawdown_pct * 100:.2f}% | CAGR: {cagr * 100:.2f}%")
    print(f"给交易所交的手续费总计: -${sum(t['fee'] for t in trade_history):.2f}")
    print(f"最终资金: ${capital:.2f} (净利: ${(capital - initial_capital):.2f})")
    print("=" * 65)