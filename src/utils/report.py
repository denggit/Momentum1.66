#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import os


def print_full_report(trade_history, df, initial_capital, capital, strategy_name, total_days):
    """
    1:1 还原用户最喜爱的深度量化报告格式，包含所有量化维度与详细时间戳
    """
    total_trades = len(trade_history)
    if total_trades == 0:
        print(f"\n=== 🚀 {strategy_name} ===")
        print("未产生任何交易，请检查策略逻辑或信号生成。")
        return

    # --- 1. 核心数据预计算 ---
    win_trades = sum(1 for t in trade_history if t['pnl'] > 0)
    gross_profit = sum(t['pnl'] for t in trade_history if t['pnl'] > 0)
    gross_loss = sum(abs(t['pnl']) for t in trade_history if t['pnl'] <= 0)
    total_fees_paid = sum(t['fee'] for t in trade_history)

    capital_curve = [initial_capital]
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    trade_returns = []

    for t in trade_history:
        pnl = t['pnl']
        cap_before = t['capital'] - pnl
        trade_returns.append(pnl / cap_before if cap_before > 0 else 0)
        if t['capital'] > peak_capital: peak_capital = t['capital']
        dd = (peak_capital - t['capital']) / peak_capital if peak_capital > 0 else 0
        max_drawdown_pct = max(max_drawdown_pct, dd)

    win_rate = win_trades / total_trades
    loss_rate = 1 - win_rate
    avg_win = gross_profit / win_trades if win_trades > 0 else 0
    avg_loss = gross_loss / (total_trades - win_trades) if (total_trades - win_trades) > 0 else 0
    pnl_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    years = total_days / 365.25
    cagr = ((capital / initial_capital) ** (1 / years) - 1) if years > 0 and capital > 0 else 0
    calmar = cagr / max_drawdown_pct if max_drawdown_pct > 0 else float('inf')

    # 计算夏普比率
    if len(trade_returns) > 1:
        std_dev = np.std(trade_returns)
        sharpe = (np.mean(trade_returns) / std_dev) * np.sqrt(total_trades * (1 / years)) if std_dev > 0 else 0
    else:
        sharpe = 0

    # --- 2. 开始打印头部 ---
    start_str = df.index[0].strftime('%Y-%m-%d')
    end_str = df.index[-1].strftime('%Y-%m-%d')
    print(f"\n=== 🚀 启动 {strategy_name} | {start_str} 至 {end_str} ({total_days:.1f} 天) ===")

    print("\n" + "=" * 65)
    print(f" 📊 {strategy_name} - 深度量化绩效报告")
    print("=" * 65)

    # --- 3. 逐年绩效拆解 ---
    print("\n" + "=" * 65)
    print(" 📅 逐年绩效拆解 (Yearly Breakdown)")
    print("=" * 65)
    print(f"{'年份':<6} | {'初始资金':<10} | {'净盈亏':<10} | {'当年收益率':<10} | {'胜率':<6} | {'最大回撤':<8}")
    print("-" * 65)

    current_year_cap = initial_capital
    for y in sorted(df.index.year.unique()):
        trades_y = [t for t in trade_history if t['exit_time'].year == y]
        if not trades_y: continue
        y_wins = sum(1 for t in trades_y if t['pnl'] > 0)
        y_pnl = sum(t['pnl'] for t in trades_y)
        y_roi = y_pnl / current_year_cap

        y_peak, y_max_dd, temp_c = current_year_cap, 0.0, current_year_cap
        for t in trades_y:
            temp_c += t['pnl']
            y_peak = max(y_peak, temp_c)
            dd = (y_peak - temp_c) / y_peak if y_peak > 0 else 0
            y_max_dd = max(y_max_dd, dd)

        print(
            f"{y:<6} | ${current_year_cap:<9.2f} | ${y_pnl:<+9.2f} | {y_roi * 100:>+8.2f}%  | {y_wins / len(trades_y) * 100:>5.1f}% | {-y_max_dd * 100:>6.2f}%")
        current_year_cap += y_pnl

    # --- 4. 核心量化指标 ---
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
    print(f"单笔期望值 (Expectancy):  +${expectancy:.2f}")

    # --- 5. 风险与财务评估 ---
    print("\n" + "-" * 65)
    print(" 🛡️ 风险与财务评估 (Risk & Finance)")
    print("-" * 65)
    print(f"最大回撤 (Max Drawdown):  {max_drawdown_pct * 100:.2f}%")
    print(f"夏普比率 (Sharpe Ratio):  {sharpe:.2f}")
    print(f"卡玛比率 (Calmar Ratio):  {calmar:.2f}")
    print(f"给交易所交的手续费总计:   -${total_fees_paid:.2f}")

    # --- 6. Top 5 史诗级战绩 ---
    for t in trade_history:
        dur = t['exit_time'] - t['entry_time']
        h = int(dur.total_seconds() // 3600)
        t['duration_str'] = f"{h // 24}天 {h % 24}小时" if h >= 24 else f"{h}小时"

    wins_top = sorted([t for t in trade_history if t['pnl'] > 0], key=lambda x: x['pnl'], reverse=True)[:5]
    loss_top = sorted([t for t in trade_history if t['pnl'] < 0], key=lambda x: x['pnl'])[:5]

    print("\n" + "🏆" * 3 + " 盈利 Top 5 史诗级交易 " + "🏆" * 3)
    print("-" * 65)
    for i, t in enumerate(wins_top):
        print(
            f"{i + 1}. [{t['type']}] 进: {t['entry_time'].strftime('%m-%d %H:%M')} | 出: {t['exit_time'].strftime('%m-%d %H:%M')} | 历时: {t['duration_str']} | 净赚: +${t['pnl']:.2f}")

    print("\n" + "🩸" * 3 + " 亏损 Top 5 极度考验 " + "🩸" * 3)
    print("-" * 65)
    for i, t in enumerate(loss_top):
        print(
            f"{i + 1}. [{t['type']}] 进: {t['entry_time'].strftime('%m-%d %H:%M')} | 出: {t['exit_time'].strftime('%m-%d %H:%M')} | 历时: {t['duration_str']} | 净亏: -${abs(t['pnl']):.2f}")

    # --- 7. 最终结算 ---
    print("\n" + "=" * 65)
    print(f"初始资金 (Initial Cap):   ${initial_capital:.2f}")
    print(f"最终资金 (Final Cap):     ${capital:.2f}")
    print(
        f"总净利润 (Net PnL):       +${(capital - initial_capital):.2f} (总收益率: {(capital - initial_capital) / initial_capital * 100:.2f}%)")
    print(f"复合年化收益率 (CAGR):    {cagr * 100:.2f}%")
    print("=" * 65)

    # --- 8. CSV 导出 ---
    export_df = pd.DataFrame(trade_history)
    export_df.rename(columns={
        'entry_time': 'Entry_Time', 'exit_time': 'Exit_Time', 'type': 'Type',
        'entry': 'Entry_Price', 'exit': 'Exit_Price', 'pnl': 'Net_PnL',
        'fee': 'Fee', 'capital': 'Capital', 'mfe_r': 'MFE(R)', 'mae_r': 'MAE(R)'
    }, inplace=True)

    safe_name = strategy_name.replace(' ', '_').replace('/', '_').replace(':', '')
    csv_filename = f"{safe_name}_TradeLog.csv"
    export_df.to_csv(csv_filename, index=False)
    print(f"\n📂 交易明细已导出至: {os.path.abspath(csv_filename)}")