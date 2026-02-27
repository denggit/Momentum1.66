#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import logging
from config.loader import SYMBOL, TIMEFRAME, SQZ_PARAMS, RISK_PARAMS
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_squeeze_indicators
from src.strategy.squeeze import SqueezeStrategy

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

LIMIT = 35040


def run_backtest(df: pd.DataFrame, initial_capital=1000.0):
    capital = initial_capital
    max_risk = 0.008  # 单笔风险定额 0.8%
    atr_multiplier = 3.0  
    fee_rate = 0.0005  # 【新增】单边手续费 0.05% (OKX Taker市价标准)
    
    in_position = False
    position_type = 0  
    entry_time = None     
    entry_price = 0.0     
    stop_loss = 0.0
    position_size_coin = 0.0
    accumulated_fee = 0.0 # 【新增】记录当前持仓累计产生的手续费
    
    trade_history = []
    
    print(f"\n=== 🚀 启动实盘级回测 | 初始资金: ${capital} | 风险定额: {max_risk*100}% | 手续费: {fee_rate*100}% ===")

    for index, row in df.iterrows():
        just_closed = False  
        
        # ==========================================
        # 1. 离场逻辑 (扣除双边手续费)
        # ==========================================
        if in_position:
            exit_price = 0.0
            is_exiting = False
            
            if position_type == 1: # -- 多头 --
                if row['low'] <= stop_loss:
                    exit_price = stop_loss
                    is_exiting = True
                else:
                    trailing_sl = row['close'] - (row['ATR'] * atr_multiplier)
                    if trailing_sl > stop_loss: stop_loss = trailing_sl  
            
            elif position_type == -1: # -- 空头 --
                if row['high'] >= stop_loss:
                    exit_price = stop_loss
                    is_exiting = True
                else:
                    trailing_sl = row['close'] + (row['ATR'] * atr_multiplier)
                    if trailing_sl < stop_loss: stop_loss = trailing_sl

            # 执行平仓与财务结算
            if is_exiting:
                # 计算总平仓手续费
                exit_fee = position_size_coin * exit_price * fee_rate
                total_trade_fee = accumulated_fee + exit_fee
                
                # 计算毛利与净利
                if position_type == 1:
                    gross_pnl = (exit_price - entry_price) * position_size_coin
                else:
                    gross_pnl = (entry_price - exit_price) * position_size_coin
                    
                net_pnl = gross_pnl - total_trade_fee # 扣除磨损！
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
        # 2. 进场/加仓逻辑 (累计开仓手续费)
        # ==========================================
        if row['Signal'] != 0:
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
                    
                    # 【记录手续费】首次开仓的磨损
                    accumulated_fee = position_size_coin * entry_price * fee_rate
            
            elif in_position and row['Signal'] == position_type and not just_closed:
                new_entry_price = row['close']
                atr_value = row['ATR']
                risk_amount_usdt = capital * max_risk
                
                if position_type == 1:
                    new_stop_loss = new_entry_price - (atr_value * atr_multiplier)
                    if new_stop_loss > stop_loss: stop_loss = new_stop_loss 
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
                        entry_price = ((entry_price * position_size_coin) + (new_entry_price * new_size)) / total_size
                        position_size_coin = total_size
                        
                        # 【记录手续费】加仓的磨损叠加
                        accumulated_fee += new_size * new_entry_price * fee_rate
                        # print(f"   [+] {index} 触发同向加仓! 最新均价变为: {entry_price:.2f} | 止损推至: {stop_loss:.2f}")

    # 期末强平逻辑也加上扣费
    if in_position:
        last_time = df.index[-1]
        last_close = df.iloc[-1]['close']
        exit_fee = position_size_coin * last_close * fee_rate
        total_trade_fee = accumulated_fee + exit_fee
        if position_type == 1:
            gross_pnl = (last_close - entry_price) * position_size_coin
        else:
            gross_pnl = (entry_price - last_close) * position_size_coin
        net_pnl = gross_pnl - total_trade_fee
        capital += net_pnl
        trade_history.append({'entry_time': entry_time, 'exit_time': last_time, 'type': 'LONG' if position_type == 1 else 'SHORT', 'entry': entry_price, 'exit': last_close, 'pnl': net_pnl, 'fee': total_trade_fee, 'capital': capital, 'note': '(期末强平)'})

    # ==========================================
    # 4. 打印专业级量化回测报告 (含手续费统计)
    # ==========================================
    print("\n" + "="*50)
    print(" 📊 Momentum 1.66 - 深度量化绩效报告 (已扣除手续费)")
    print("="*50)
    
    win_trades = 0
    total_trades = len(trade_history)
    gross_profit = 0.0
    gross_loss = 0.0
    total_fees_paid = 0.0  # 累计总手续费
    
    capital_curve = [initial_capital]
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    trade_returns = []  
    
    for t in trade_history:
        pnl = t['pnl']
        total_fees_paid += t['fee']
        if pnl > 0: 
            win_trades += 1
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)
            
        capital_before_trade = t['capital'] - pnl
        trade_returns.append(pnl / capital_before_trade)
        
        capital_curve.append(t['capital'])
        if t['capital'] > peak_capital:
            peak_capital = t['capital']
        drawdown = (peak_capital - t['capital']) / peak_capital
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown
            
        res = "盈利" if pnl > 0 else "亏损"
        note = t.get('note', '')
        # print(f"[进 {t['entry_time']} -> 出 {t['exit_time']}] {t['type']} | 均价: {t['entry']:.2f} | 净盈亏: {pnl:+.2f} U ({res}) | 磨损: -{t['fee']:.2f} U")
    
    if total_trades > 0:
        win_rate = win_trades / total_trades
        loss_rate = 1 - win_rate
        avg_win = gross_profit / win_trades if win_trades > 0 else 0
        avg_loss = gross_loss / (total_trades - win_trades) if (total_trades - win_trades) > 0 else 0
        
        pnl_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        expected_value_u = (win_rate * avg_win) - (loss_rate * avg_loss)
        
        if len(trade_returns) > 1:
            std_dev = np.std(trade_returns)
            sharpe_ratio = np.mean(trade_returns) / std_dev if std_dev > 0 else 0
            annualized_sharpe = sharpe_ratio * np.sqrt(total_trades)
        else:
            annualized_sharpe = 0.0
            
        net_profit_pct = (capital - initial_capital) / initial_capital
        calmar_ratio = net_profit_pct / max_drawdown_pct if max_drawdown_pct > 0 else float('inf')
        
        print("\n" + "-"*50)
        print(" 📈 核心量化指标 (Core Metrics)")
        print("-"*50)
        print(f"总交易次数 (Total Trades):  {total_trades}")
        print(f"胜率 (Win Rate):          {win_rate*100:.2f}%")
        print(f"平均净盈利 (Avg Win):     +${avg_win:.2f}")
        print(f"平均净亏损 (Avg Loss):    -${avg_loss:.2f}")
        print(f"净盈亏比 (PnL Ratio):     {pnl_ratio:.2f}")
        print(f"盈利因子 (Profit Factor): {profit_factor:.2f}")
        print(f"单笔期望值 (Expectancy):  +${expected_value_u:.2f}")
        print("-"*50)
        print(" 🛡️ 风险与财务评估 (Risk & Finance)")
        print("-"*50)
        print(f"最大回撤 (Max Drawdown):  {max_drawdown_pct*100:.2f}%")
        print(f"夏普比率 (Sharpe Ratio):  {annualized_sharpe:.2f}")
        print(f"卡玛比率 (Calmar Ratio):  {calmar_ratio:.2f}")
        print(f"给交易所交的手续费总计:   -${total_fees_paid:.2f} ⚠️")
        print("-"*50)
        print(f"初始资金 (Initial Cap):   ${initial_capital:.2f}")
        print(f"最终资金 (Final Cap):     ${capital:.2f}")
        print(f"总净利润 (Net Profit):    ${(capital - initial_capital):.2f} ({net_profit_pct*100:.2f}%)")
        print("="*50)
    else:
        print("无交易发生。")


if __name__ == "__main__":
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=TIMEFRAME)
    df = loader.fetch_historical_data(limit=LIMIT)

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
