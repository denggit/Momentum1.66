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

    print(f"\n=== 🚀 启动 {strategy_name} | {start_time_str} 至 {end_time_str} ({total_days:.1f} 天) ===")
    tp_str = f"{target_r}R" if target_r else "无(纯追踪)"
    print(f"初始资金: ${capital} | 单笔风控: {max_risk * 100}% | ATR追踪: {atr_multiplier}x | 强制止盈: {tp_str}")

    for index, row in df.iterrows():
        just_closed = False

        # 【新增】只要在持仓中，实时更新这笔交易经历过的最高价和最低价
        if in_position:
            if row['high'] > trade_max_price: trade_max_price = row['high']
            if row['low'] < trade_min_price: trade_min_price = row['low']

        # ==========================================
        # 1. 离场逻辑 (带插针识别)
        # ==========================================

        # ==========================================
        # 1. 离场逻辑 (带插针识别)
        # ==========================================
        if in_position:
            exit_price = 0.0
            is_exiting = False

            if position_type == 1:
                tp_price = entry_price + (initial_risk_per_coin * target_r) if target_r else float('inf')
                if target_r is not None and row['high'] >= tp_price:
                    exit_price = tp_price
                    is_exiting = True
                elif row['low'] <= stop_loss:
                    exit_price = stop_loss
                    is_exiting = True
                else:
                    trailing_sl = row['close'] - (row['ATR'] * atr_multiplier)
                    if trailing_sl > stop_loss: stop_loss = trailing_sl

            elif position_type == -1:
                tp_price = entry_price - (initial_risk_per_coin * target_r) if target_r else -float('inf')
                if target_r is not None and row['low'] <= tp_price:
                    exit_price = tp_price
                    is_exiting = True
                elif row['high'] >= stop_loss:
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

                # 【新增】计算 MFE 和 MAE (单位: R倍数，即赚/亏了初始风控的多少倍)
                if position_type == 1:
                    mfe_r = (trade_max_price - entry_price) / initial_risk_per_coin
                    mae_r = (entry_price - trade_min_price) / initial_risk_per_coin
                else:
                    mfe_r = (entry_price - trade_min_price) / initial_risk_per_coin
                    mae_r = (trade_max_price - entry_price) / initial_risk_per_coin

                # 【新增】计算持仓时间 (小时)
                hold_hours = round((index - entry_time).total_seconds() / 3600, 1)

                trade_history.append({
                    'entry_time': entry_time,
                    'exit_time': index,
                    'type': 'LONG' if position_type == 1 else 'SHORT',
                    'entry': entry_price,
                    'exit': exit_price,
                    'pnl': net_pnl,
                    'fee': total_trade_fee,
                    'capital': capital,
                    'hold_hours': hold_hours,
                    'mfe_r': round(mfe_r, 2),  # 最大潜在盈利 (R)
                    'mae_r': round(mae_r, 2)  # 最大潜在亏损 (R)
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
                # 【新增】：如果有专属止损价，就用专属的！否则用 4.5x ATR 宽止损
                if 'SL_Price' in df.columns and not pd.isna(row['SL_Price']):
                    stop_loss = row['SL_Price']
                else:
                    stop_loss = entry_price - (atr_value * atr_multiplier)
                sl_distance = entry_price - stop_loss

            elif row['Signal'] == -1:
                position_type = -1
                # 【新增】：同理
                if 'SL_Price' in df.columns and not pd.isna(row['SL_Price']):
                    stop_loss = row['SL_Price']
                else:
                    stop_loss = entry_price + (atr_value * atr_multiplier)
                sl_distance = stop_loss - entry_price

            if sl_distance > 0:
                position_size_coin = risk_amount_usdt / sl_distance
                if (position_size_coin * entry_price / capital) > 10:
                    position_size_coin = (capital * 10) / entry_price
                in_position = True
                initial_risk_per_coin = sl_distance
                accumulated_fee = position_size_coin * entry_price * fee_rate

                # 【新增】进场时，初始化这笔交易的极值记录
                trade_max_price = entry_price
                trade_min_price = entry_price

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
             'entry': entry_price, 'exit': last_close, 'pnl': net_pnl, 'fee': total_trade_fee, 'capital': capital,
             'note': '(强平)'})

    # ==========================================
    # 3. 打印专业级量化回测报告
    # ==========================================
    print("\n" + "=" * 65)
    print(f" 📊 {strategy_name} - 深度量化绩效报告")
    print("=" * 65)

    total_trades = len(trade_history)
    if total_trades == 0:
        print("没有产生任何交易。")
        return

    win_trades = 0
    gross_profit = 0.0
    gross_loss = 0.0
    total_fees_paid = 0.0

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
        trade_returns.append(pnl / capital_before_trade if capital_before_trade > 0 else 0)

        capital_curve.append(t['capital'])
        if t['capital'] > peak_capital:
            peak_capital = t['capital']
        drawdown = (peak_capital - t['capital']) / peak_capital if peak_capital > 0 else 0
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown

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
        annualized_sharpe = sharpe_ratio * np.sqrt(total_trades * (365.25 / total_days))
    else:
        annualized_sharpe = 0.0

    net_profit_pct = (capital - initial_capital) / initial_capital

    years = total_days / 365.25
    cagr = ((capital / initial_capital) ** (1 / years) - 1) if years > 0 and capital > 0 else 0
    calmar_ratio = cagr / max_drawdown_pct if max_drawdown_pct > 0 else float('inf')

    # --- 逐年绩效拆解 ---
    print("\n" + "=" * 65)
    print(" 📅 逐年绩效拆解 (Yearly Breakdown)")
    print("=" * 65)
    print(f"{'年份':<6} | {'初始资金':<10} | {'净盈亏':<10} | {'当年收益率':<10} | {'胜率':<6} | {'最大回撤':<8}")
    print("-" * 65)

    current_year_cap = initial_capital
    for y in sorted(df.index.year.unique()):
        trades_y = [t for t in trade_history if t['exit_time'].year == y]
        if not trades_y:
            continue

        y_wins = sum(1 for t in trades_y if t['pnl'] > 0)
        y_trades = len(trades_y)
        y_win_rate = y_wins / y_trades if y_trades > 0 else 0
        y_net_pnl = sum(t['pnl'] for t in trades_y)
        y_roi = y_net_pnl / current_year_cap if current_year_cap > 0 else 0

        y_peak = current_year_cap
        y_max_dd = 0.0
        temp_cap = current_year_cap
        for t in trades_y:
            temp_cap += t['pnl']
            if temp_cap > y_peak:
                y_peak = temp_cap
            dd = (y_peak - temp_cap) / y_peak if y_peak > 0 else 0
            if dd > y_max_dd:
                y_max_dd = dd

        print(
            f"{y:<6} | ${current_year_cap:<9.2f} | ${y_net_pnl:<+9.2f} | {y_roi * 100:>+8.2f}%  | {y_win_rate * 100:>5.1f}% | {-y_max_dd * 100:>6.2f}%")
        current_year_cap += y_net_pnl

    print("\n" + "-" * 65)
    print(" 📈 核心量化指标 (Core Metrics)")
    print("-" * 65)
    print(f"测试跨度 (Duration):      {total_days:.1f} 天 ({years:.2f} 年)")
    print(f"总交易次数 (Total Trades):  {total_trades}")
    print(f"胜率 (Win Rate):          {win_rate * 100:.2f}%")
    print(f"平均净盈利 (Avg Win):     +${avg_win:.2f}")
    print(f"平均净亏损 (Avg Loss):    -${avg_loss:.2f}")
    print(f"净盈亏比 (PnL Ratio):     {pnl_ratio:.2f}")
    print(f"盈利因子 (Profit Factor): {profit_factor:.2f}")
    print(f"单笔期望值 (Expectancy):  +${expected_value_u:.2f}")

    print("\n" + "-" * 65)
    print(" 🛡️ 风险与财务评估 (Risk & Finance)")
    print("-" * 65)
    print(f"最大回撤 (Max Drawdown):  {max_drawdown_pct * 100:.2f}%")
    print(f"夏普比率 (Sharpe Ratio):  {annualized_sharpe:.2f}")
    print(f"卡玛比率 (Calmar Ratio):  {calmar_ratio:.2f}")
    print(f"给交易所交的手续费总计:   -${total_fees_paid:.2f}")

    for t in trade_history:
        try:
            duration = t['exit_time'] - t['entry_time']
            total_hours = int(duration.total_seconds() // 3600)
            days = total_hours // 24
            hours = total_hours % 24
            t['duration_str'] = f"{days}天 {hours}小时" if days > 0 else f"{hours}小时"
        except:
            t['duration_str'] = "未知"

    sorted_by_pnl = sorted(trade_history, key=lambda x: x['pnl'], reverse=True)
    top_5_wins = [t for t in sorted_by_pnl if t['pnl'] > 0][:5]
    sorted_by_loss = sorted(trade_history, key=lambda x: x['pnl'])
    top_5_losses = [t for t in sorted_by_loss if t['pnl'] < 0][:5]

    print("\n" + "🏆" * 3 + " 盈利 Top 5 史诗级交易 " + "🏆" * 3)
    print("-" * 65)
    for i, t in enumerate(top_5_wins):
        print(
            f"{i + 1}. [{t['type']}] 进: {t['entry_time'].strftime('%m-%d %H:%M')} | 出: {t['exit_time'].strftime('%m-%d %H:%M')} | 历时: {t['duration_str']} | 净赚: +${t['pnl']:.2f}")

    print("\n" + "🩸" * 3 + " 亏损 Top 5 极度考验 " + "🩸" * 3)
    print("-" * 65)
    for i, t in enumerate(top_5_losses):
        print(
            f"{i + 1}. [{t['type']}] 进: {t['entry_time'].strftime('%m-%d %H:%M')} | 出: {t['exit_time'].strftime('%m-%d %H:%M')} | 历时: {t['duration_str']} | 净亏: -${abs(t['pnl']):.2f}")

    print("\n" + "=" * 65)
    print(f"初始资金 (Initial Cap):   ${initial_capital:.2f}")
    print(f"最终资金 (Final Cap):     ${capital:.2f}")
    print(f"总净利润 (Net PnL):       +${(capital - initial_capital):.2f} (总收益率: {net_profit_pct * 100:.2f}%)")
    print(f"复合年化收益率 (CAGR):    {cagr * 100:.2f}%")
    print("=" * 65)

    # 【新增】将逐笔交易明细导出为 CSV 文件，供 Excel 深度分析！
    if len(trade_history) > 0:
        import os
        export_df = pd.DataFrame(trade_history)

        # 把代码内部用的全小写 key 重命名为好看的专业表头
        export_df.rename(columns={
            'entry_time': 'Entry_Time',
            'exit_time': 'Exit_Time',
            'type': 'Type',
            'entry': 'Entry_Price',
            'exit': 'Exit_Price',
            'pnl': 'Net_PnL',
            'fee': 'Fee',
            'capital': 'Capital',
            'hold_hours': 'Hold_Hours',
            'mfe_r': 'MFE(R)',
            'mae_r': 'MAE(R)',
            'note': 'Note'
        }, inplace=True)

        # 去掉策略名中可能导致文件名非法的字符
        safe_name = strategy_name.replace(' ', '_').replace('/', '_').replace(':', '')
        csv_filename = f"{safe_name}_TradeLog.csv"
        export_df.to_csv(csv_filename, index=False)
        print(f"\n📂 交易明细已导出至: {os.path.abspath(csv_filename)}")
        print("💡 建议使用 Excel 打开，重点分析 MFE(R) 和 MAE(R) 列寻找优化灵感！")
