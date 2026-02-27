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
    max_risk = RISK_PARAMS['max_risk_per_trade']
    atr_multiplier = 3.0  # 3倍ATR吊灯止损

    in_position = False
    position_type = 0
    entry_time = None
    entry_price = 0.0  # 记录均价
    stop_loss = 0.0
    position_size_coin = 0.0

    trade_history = []

    print(
        f"\n=== 🚀 启动回测 | 初始资金: ${capital} | 风险定额: {max_risk * 100}% | 吊灯止损: {atr_multiplier}x ATR | 开启同向加仓 ===")

    for index, row in df.iterrows():
        just_closed = False

        # ==========================================
        # 1. 离场逻辑：严格遵守时序（先检查存活，再更新防守）
        # ==========================================
        if in_position:
            if position_type == 1:  # -- 多头 --
                # 【先检查】：这根线的下探有没有打穿“老止损线”？
                if row['low'] <= stop_loss:
                    exit_price = stop_loss
                    pnl = (exit_price - entry_price) * position_size_coin
                    capital += pnl
                    trade_history.append(
                        {'entry_time': entry_time, 'exit_time': index, 'type': 'LONG', 'entry': entry_price,
                         'exit': exit_price, 'pnl': pnl, 'capital': capital})
                    in_position = False
                    just_closed = True
                else:
                    # 【活下来了】：用这根线的最高价，去抬高“新止损线”
                    trailing_sl = row['high'] - (row['ATR'] * atr_multiplier)
                    if trailing_sl > stop_loss:
                        stop_loss = trailing_sl

            elif position_type == -1:  # -- 空头 --
                if row['high'] >= stop_loss:
                    exit_price = stop_loss
                    pnl = (entry_price - exit_price) * position_size_coin
                    capital += pnl
                    trade_history.append(
                        {'entry_time': entry_time, 'exit_time': index, 'type': 'SHORT', 'entry': entry_price,
                         'exit': exit_price, 'pnl': pnl, 'capital': capital})
                    in_position = False
                    just_closed = True
                else:
                    trailing_sl = row['low'] + (row['ATR'] * atr_multiplier)
                    if trailing_sl < stop_loss:
                        stop_loss = trailing_sl

        # ==========================================
        # 2. 进场/加仓逻辑：寻找信号
        # ==========================================
        if row['Signal'] != 0:

            # 【A】如果空仓 (或者是同一根K线刚被扫损出局)，正常开新仓
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
                    if (position_size_coin * entry_price / capital) > RISK_PARAMS['max_leverage']:
                        position_size_coin = (capital * RISK_PARAMS['max_leverage']) / entry_price
                    in_position = True

            # 【B】如果正在持仓，且新信号方向一致 -> 触发金字塔加仓！
            elif in_position and row['Signal'] == position_type and not just_closed:
                new_entry_price = row['close']
                atr_value = row['ATR']
                risk_amount_usdt = capital * max_risk

                if position_type == 1:
                    new_stop_loss = new_entry_price - (atr_value * atr_multiplier)
                    if new_stop_loss > stop_loss: stop_loss = new_stop_loss  # 暴力上移防守
                    sl_distance = new_entry_price - stop_loss
                else:
                    new_stop_loss = new_entry_price + (atr_value * atr_multiplier)
                    if new_stop_loss < stop_loss: stop_loss = new_stop_loss
                    sl_distance = stop_loss - new_entry_price

                if sl_distance > 0:
                    new_size = risk_amount_usdt / sl_distance
                    total_notional = (position_size_coin + new_size) * new_entry_price
                    if (total_notional / capital) > RISK_PARAMS['max_leverage']:
                        allowed_total_size = (capital * RISK_PARAMS['max_leverage']) / new_entry_price
                        new_size = allowed_total_size - position_size_coin

                    if new_size > 0:
                        total_size = position_size_coin + new_size
                        # 重新计算均价
                        entry_price = ((entry_price * position_size_coin) + (new_entry_price * new_size)) / total_size
                        position_size_coin = total_size
                        print(
                            f"   [+] {index} 触发同向加仓! 最新均价变为: {entry_price:.2f} | 止损推至: {stop_loss:.2f}")

    # ==========================================
    # 3. 期末强制平仓结算 (循环结束后的逻辑)
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
    # 4. 打印专业级回测报告 (Phase 1 终极产出)
    # ==========================================
    print("\n" + "="*50)
    print(" 📊 项目 1.66 - Phase 1 阶段性回测报告")
    print("="*50)
    
    win_trades = 0
    total_trades = len(trade_history)
    gross_profit = 0.0
    gross_loss = 0.0
    
    # 记录资金曲线以计算最大回撤
    capital_curve = [initial_capital]
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    
    for t in trade_history:
        res = "盈利" if t['pnl'] > 0 else "亏损"
        if t['pnl'] > 0: 
            win_trades += 1
            gross_profit += t['pnl']
        else:
            gross_loss += abs(t['pnl'])
            
        note = t.get('note', '')
        print(f"[进 {t['entry_time']} -> 出 {t['exit_time']}] {t['type']} | 均价: {t['entry']:.2f} | 出价: {t['exit']:.2f} | 盈亏: {t['pnl']:+.2f} U ({res}) {note} | 余额: {t['capital']:.2f} U")
        
        # 计算回撤
        capital_curve.append(t['capital'])
        if t['capital'] > peak_capital:
            peak_capital = t['capital']
        drawdown = (peak_capital - t['capital']) / peak_capital
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown
    
    if total_trades > 0:
        win_rate = win_trades / total_trades
        pnl_ratio = (gross_profit / win_trades) / (gross_loss / (total_trades - win_trades)) if (total_trades - win_trades) > 0 and win_trades > 0 else float('inf')
        
        print("\n" + "-"*50)
        print(" 📈 核心绩效指标 (Core Metrics)")
        print("-"*50)
        print(f"测试周期: 近 {limit} 根 K 线")
        print(f"总交易次数: {total_trades}")
        print(f"胜率 (Win Rate): {win_rate*100:.2f}%")
        print(f"盈亏比 (PnL Ratio): {pnl_ratio:.2f}")
        print(f"最大回撤 (Max Drawdown): {max_drawdown_pct*100:.2f}%")
        print(f"初始资金: ${initial_capital:.2f}")
        print(f"最终资金: ${capital:.2f}")
        print(f"总净利润: ${(capital - initial_capital):.2f} ({(capital/initial_capital - 1)*100:.2f}%)")
        print("="*50)
    else:
        print("无交易发生。")


if __name__ == "__main__":
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=TIMEFRAME)
    df = loader.fetch_historical_data(limit=17500)

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
