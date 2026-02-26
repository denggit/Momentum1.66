#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd
import logging
from config.loader import SYMBOL, TIMEFRAME, SQZ_PARAMS, RISK_PARAMS
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_squeeze_indicators
from src.strategy.squeeze import SqueezeStrategy

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def run_backtest(df: pd.DataFrame, initial_capital=1000.0):
    capital = initial_capital
    max_risk = RISK_PARAMS['max_risk_per_trade']  # 0.0166
    atr_multiplier = 3.0  # 3倍ATR吊灯止损

    in_position = False
    position_type = 0
    entry_time = None
    entry_price = 0.0  # 现在的含义是：平均持仓成本
    stop_loss = 0.0
    position_size_coin = 0.0

    trade_history = []

    print(
        f"\n=== 🚀 启动回测 | 初始资金: ${capital} | 风险定额: {max_risk * 100}% | 吊灯止损: {atr_multiplier}x ATR | 开启同向加仓 ===")

    for index, row in df.iterrows():
        just_closed = False

        # ==========================================
        # 1. 离场逻辑：检查是否触发吊灯止损
        # ==========================================
        if in_position:
            if position_type == 1:  # -- 多头 --
                trailing_sl = row['high'] - (row['ATR'] * atr_multiplier)
                if trailing_sl > stop_loss: stop_loss = trailing_sl

                if row['low'] <= stop_loss:
                    exit_price = stop_loss
                    pnl = (exit_price - entry_price) * position_size_coin
                    capital += pnl
                    trade_history.append(
                        {'entry_time': entry_time, 'exit_time': index, 'type': 'LONG', 'entry': entry_price,
                         'exit': exit_price, 'pnl': pnl, 'capital': capital})
                    in_position = False
                    just_closed = True

            elif position_type == -1:  # -- 空头 --
                trailing_sl = row['low'] + (row['ATR'] * atr_multiplier)
                if trailing_sl < stop_loss: stop_loss = trailing_sl

                if row['high'] >= stop_loss:
                    exit_price = stop_loss
                    pnl = (entry_price - exit_price) * position_size_coin
                    capital += pnl
                    trade_history.append(
                        {'entry_time': entry_time, 'exit_time': index, 'type': 'SHORT', 'entry': entry_price,
                         'exit': exit_price, 'pnl': pnl, 'capital': capital})
                    in_position = False
                    just_closed = True

            if just_closed:
                continue  # 如果刚刚平仓，直接进入下一根K线

        # ==========================================
        # 2. 进场/加仓逻辑：寻找信号
        # ==========================================
        if row['Signal'] != 0:

            # 【A】如果空仓，正常首次开仓
            if not in_position:
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

                    # 初始杠杆检查
                    if (position_size_coin * entry_price / capital) > RISK_PARAMS['max_leverage']:
                        position_size_coin = (capital * RISK_PARAMS['max_leverage']) / entry_price
                    in_position = True

            # 【B】如果持有仓位，且新信号与当前方向一致 -> 执行加仓！
            elif in_position and row['Signal'] == position_type:
                new_entry_price = row['close']
                atr_value = row['ATR']
                risk_amount_usdt = capital * max_risk

                # 重新计算基于新加仓价的止损
                if position_type == 1:
                    new_stop_loss = new_entry_price - (atr_value * atr_multiplier)
                    if new_stop_loss > stop_loss:
                        stop_loss = new_stop_loss  # 暴力上移防守线
                    sl_distance = new_entry_price - stop_loss
                else:
                    new_stop_loss = new_entry_price + (atr_value * atr_multiplier)
                    if new_stop_loss < stop_loss:
                        stop_loss = new_stop_loss  # 暴力下移防守线
                    sl_distance = stop_loss - new_entry_price

                if sl_distance > 0:
                    new_size = risk_amount_usdt / sl_distance

                    # 加仓时的总杠杆安全阀
                    total_notional = (position_size_coin + new_size) * new_entry_price
                    if (total_notional / capital) > RISK_PARAMS['max_leverage']:
                        # 如果满仓了，只加允许的剩余额度
                        allowed_total_size = (capital * RISK_PARAMS['max_leverage']) / new_entry_price
                        new_size = allowed_total_size - position_size_coin

                    if new_size > 0:
                        # 重新计算加权平均成本价！
                        total_size = position_size_coin + new_size
                        entry_price = ((entry_price * position_size_coin) + (new_entry_price * new_size)) / total_size
                        position_size_coin = total_size
                        print(
                            f"   [+] {index} 触发同向加仓! 最新均价变为: {entry_price:.2f} | 止损位推至: {stop_loss:.2f}")

    # ==========================================
    # 3. 回测结束：期末强平逻辑 (防止盈利的单子隐身)
    # ==========================================
    if in_position:
        last_time = df.index[-1]
        last_close = df.iloc[-1]['close']
        exit_price = last_close
        if position_type == 1:
            pnl = (exit_price - entry_price) * position_size_coin
        else:
            pnl = (entry_price - exit_price) * position_size_coin
        capital += pnl
        trade_history.append(
            {'entry_time': entry_time, 'exit_time': last_time, 'type': 'LONG' if position_type == 1 else 'SHORT',
             'entry': entry_price, 'exit': exit_price, 'pnl': pnl, 'capital': capital, 'note': '(期末强平)'})

    # ==========================================
    # 4. 打印回测报告
    # ==========================================
    print("\n=== 回测交易日志 ===")
    win_trades = 0
    total_trades = len(trade_history)
    for t in trade_history:
        res = "盈利" if t['pnl'] > 0 else "亏损"
        if t['pnl'] > 0: win_trades += 1
        note = t.get('note', '')
        print(
            f"[进 {t['entry_time']} -> 出 {t['exit_time']}] {t['type']} | 均价: {t['entry']:.2f} | 出价: {t['exit']:.2f} | 盈亏: {t['pnl']:+.2f} U ({res}) {note} | 余额: {t['capital']:.2f} U")

    if total_trades > 0:
        win_rate = win_trades / total_trades
        print("\n=== 核心绩效指标 ===")
        print(f"总交易次数: {total_trades}")
        print(f"胜率: {win_rate * 100:.2f}%")
        print(f"初始资金: ${initial_capital:.2f}")
        print(f"最终资金: ${capital:.2f}")
        print(f"净利润: ${(capital - initial_capital):.2f} ({(capital / initial_capital - 1) * 100:.2f}%)")
    else:
        print("无交易发生。")


if __name__ == "__main__":
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=TIMEFRAME)
    df = loader.fetch_historical_data(limit=5000)

    if not df.empty:
        df = add_squeeze_indicators(
            df=df,
            bb_len=SQZ_PARAMS['bb_length'],
            bb_std=SQZ_PARAMS['bb_std'],
            kc_len=SQZ_PARAMS['kc_length'],
            kc_mult=SQZ_PARAMS['kc_mult']
        )
        strategy = SqueezeStrategy(volume_factor=SQZ_PARAMS['volume_factor'])
        df = strategy.generate_signals(df)

        run_backtest(df, initial_capital=1000.0)