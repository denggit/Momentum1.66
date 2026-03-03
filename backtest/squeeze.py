#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

import numpy as np
import pandas as pd

from config.loader import GLOBAL_SETTINGS, load_strategy_config
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_squeeze_indicators
from src.strategy.squeeze import SqueezeStrategy
from src.utils.log import get_logger
logger = get_logger(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==========================================
# ⚙️ 核心回测时间控制
# ==========================================
START_DATE = '2020-01-01'  # 回测开始日期
END_DATE = '2026-02-27'  # 回测结束日期
# 设置一个足够大的 LIMIT，确保能拉取到 START_DATE 之前的数据
# (1小时级别，一年约 8760 根，50000 根约等于 5.7 年)
FETCH_LIMIT = 200000


def run_backtest(df: pd.DataFrame, initial_capital=1000.0):
    risk_params = GLOBAL_SETTINGS.get("risk")
    capital = initial_capital
    max_risk = risk_params["max_risk_per_trade"]  # 单笔风险定额 2%
    atr_multiplier = risk_params["atr_multiplier"]
    fee_rate = GLOBAL_SETTINGS.get("fee_rate", 0.0005)  # 单边手续费 0.05% (OKX Taker市价标准)

    in_position = False
    position_type = 0
    entry_time = None
    entry_price = 0.0
    stop_loss = 0.0
    position_size_coin = 0.0
    accumulated_fee = 0.0

    trade_history = []

    start_time_str = df.index[0].strftime('%Y-%m-%d')
    end_time_str = df.index[-1].strftime('%Y-%m-%d')
    total_days = (df.index[-1] - df.index[0]).total_seconds() / (24 * 3600)

    logger.info(f"\n=== 🚀 启动实盘级回测 | {start_time_str} 至 {end_time_str} ({total_days:.1f} 天) ===")
    logger.info(f"初始资金: ${capital} | 风险定额: {max_risk * 100}% | 手续费: {fee_rate * 100}%")

    for index, row in df.iterrows():
        just_closed = False

        # ==========================================
        # 1. 离场逻辑 (扣除双边手续费)
        # ==========================================
        if in_position:
            exit_price = 0.0
            is_exiting = False

            if position_type == 1:  # -- 多头 --
                if row['low'] <= stop_loss:
                    exit_price = stop_loss
                    is_exiting = True
                else:
                    trailing_sl = row['close'] - (row['ATR'] * atr_multiplier)
                    if trailing_sl > stop_loss: stop_loss = trailing_sl

            elif position_type == -1:  # -- 空头 --
                if row['high'] >= stop_loss:
                    exit_price = stop_loss
                    is_exiting = True
                else:
                    trailing_sl = row['close'] + (row['ATR'] * atr_multiplier)
                    if trailing_sl < stop_loss: stop_loss = trailing_sl

            # 执行平仓与财务结算
            if is_exiting:
                exit_fee = position_size_coin * exit_price * fee_rate
                total_trade_fee = accumulated_fee + exit_fee

                if position_type == 1:
                    gross_pnl = (exit_price - entry_price) * position_size_coin
                else:
                    gross_pnl = (entry_price - exit_price) * position_size_coin

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
                    if (position_size_coin * entry_price / capital) > risk_params['max_leverage']:
                        position_size_coin = (capital * risk_params['max_leverage']) / entry_price
                    in_position = True

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
                    if (total_notional / capital) > risk_params['max_leverage']:
                        allowed_total_size = (capital * risk_params['max_leverage']) / new_entry_price
                        new_size = allowed_total_size - position_size_coin

                    if new_size > 0:
                        total_size = position_size_coin + new_size
                        entry_price = ((entry_price * position_size_coin) + (new_entry_price * new_size)) / total_size
                        position_size_coin = total_size
                        accumulated_fee += new_size * new_entry_price * fee_rate

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
        trade_history.append(
            {'entry_time': entry_time, 'exit_time': last_time, 'type': 'LONG' if position_type == 1 else 'SHORT',
             'entry': entry_price, 'exit': last_close, 'pnl': net_pnl, 'fee': total_trade_fee, 'capital': capital,
             'note': '(期末强平)'})

    # ==========================================
    # 4. 打印专业级量化回测报告
    # ==========================================
    logger.info("\n" + "=" * 65)
    logger.info(f" 📊 Momentum {GLOBAL_SETTINGS.get('timeframe')} 引擎 - 多年期量化绩效报告")
    logger.info("=" * 65)

    win_trades = 0
    total_trades = len(trade_history)
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
        trade_returns.append(pnl / capital_before_trade)

        capital_curve.append(t['capital'])
        if t['capital'] > peak_capital:
            peak_capital = t['capital']
        drawdown = (peak_capital - t['capital']) / peak_capital
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown

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
            annualized_sharpe = sharpe_ratio * np.sqrt(total_trades * (365.25 / total_days))
        else:
            annualized_sharpe = 0.0

        net_profit_pct = (capital - initial_capital) / initial_capital

        # 计算复合年化收益率 (CAGR)
        years = total_days / 365.25
        cagr = ((capital / initial_capital) ** (1 / years) - 1) if years > 0 and capital > 0 else 0

        calmar_ratio = cagr / max_drawdown_pct if max_drawdown_pct > 0 else float('inf')

        # --- 【新增】逐年绩效拆解逻辑 ---
        logger.info("\n" + "=" * 65)
        logger.info(" 📅 逐年绩效拆解 (Yearly Breakdown)")
        logger.info("=" * 65)
        logger.info(f"{'年份':<6} | {'初始资金':<10} | {'净盈亏':<10} | {'当年收益率':<10} | {'胜率':<6} | {'最大回撤':<8}")
        logger.info("-" * 65)

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

            # 计算当年的绝对最大回撤
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

            logger.info(
                f"{y:<6} | ${current_year_cap:<9.2f} | ${y_net_pnl:<+9.2f} | {y_roi * 100:>+8.2f}%  | {y_win_rate * 100:>5.1f}% | {-y_max_dd * 100:>6.2f}%")

            # 更新下一年的初始资金
            current_year_cap += y_net_pnl

        logger.info("\n" + "-" * 65)
        logger.info(" 📈 核心量化指标 (Core Metrics)")
        logger.info("-" * 65)
        logger.info(f"测试跨度 (Duration):      {total_days:.1f} 天 ({years:.2f} 年)")
        logger.info(f"总交易次数 (Total Trades):  {total_trades}")
        logger.info(f"胜率 (Win Rate):          {win_rate * 100:.2f}%")
        logger.info(f"平均净盈利 (Avg Win):     +${avg_win:.2f}")
        logger.info(f"平均净亏损 (Avg Loss):    -${avg_loss:.2f}")
        logger.info(f"净盈亏比 (PnL Ratio):     {pnl_ratio:.2f}")
        logger.info(f"盈利因子 (Profit Factor): {profit_factor:.2f}")

        logger.info("\n" + "-" * 65)
        logger.info(" 🛡️ 风险与财务评估 (Risk & Finance)")
        logger.info("-" * 65)
        logger.info(f"最大回撤 (Max Drawdown):  {max_drawdown_pct * 100:.2f}%")
        logger.info(f"夏普比率 (Sharpe Ratio):  {annualized_sharpe:.2f}")
        logger.info(f"卡玛比率 (Calmar Ratio):  {calmar_ratio:.2f}")
        logger.info(f"给交易所交的手续费总计:   -${total_fees_paid:.2f}")

        # 计算 Top 5 交易
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

        logger.info("\n" + "🏆" * 3 + " 盈利 Top 5 史诗级交易 " + "🏆" * 3)
        logger.info("-" * 65)
        for i, t in enumerate(top_5_wins):
            logger.info(
                f"{i + 1}. [{t['type']}] 进: {t['entry_time'].strftime('%m-%d %H:%M')} | 出: {t['exit_time'].strftime('%m-%d %H:%M')} | 历时: {t['duration_str']} | 净赚: +${t['pnl']:.2f}")

        logger.info("\n" + "🩸" * 3 + " 亏损 Top 5 极度考验 " + "🩸" * 3)
        logger.info("-" * 65)
        for i, t in enumerate(top_5_losses):
            logger.info(
                f"{i + 1}. [{t['type']}] 进: {t['entry_time'].strftime('%m-%d %H:%M')} | 出: {t['exit_time'].strftime('%m-%d %H:%M')} | 历时: {t['duration_str']} | 净亏: -${abs(t['pnl']):.2f}")

        logger.info("\n" + "=" * 65)
        logger.info(f"初始资金 (Initial Cap):   ${initial_capital:.2f}")
        logger.info(f"最终资金 (Final Cap):     ${capital:.2f}")
        logger.info(f"总净利润 (Net PnL):       +${(capital - initial_capital):.2f} (总收益率: {net_profit_pct * 100:.2f}%)")
        logger.info(f"复合年化收益率 (CAGR):    {cagr * 100:.2f}%  <--- 🚀 华尔街核心考核指标")
        logger.info("=" * 65)
    else:
        logger.info("无交易发生。")


if __name__ == "__main__":
    sqz_params = load_strategy_config("squeeze", GLOBAL_SETTINGS.get("symbol")).get("strategy", {})
    # 使用智能日期范围拉取数据
    loader = OKXDataLoader(symbol=GLOBAL_SETTINGS.get("symbol"), timeframe=GLOBAL_SETTINGS.get("timeframe"))
    df = loader.fetch_data_by_date_range(START_DATE, END_DATE)

    if df.empty:
        logger.info(f"错误：无法获取 {START_DATE} 到 {END_DATE} 的数据，请检查日期或网络连接！")
    else:
        df = add_squeeze_indicators(
            df=df,
            bb_len=sqz_params['bb_length'],
            bb_std=sqz_params['bb_std'],
            kc_len=sqz_params['kc_length'],
            kc_mult=sqz_params['kc_mult']
        )
        strategy = SqueezeStrategy(volume_factor=sqz_params['volume_factor'])
        df = strategy.generate_signals(df, sqz_params["min_squeeze_duration"], sqz_params["min_adx"])

        run_backtest(df, initial_capital=1000.0)
