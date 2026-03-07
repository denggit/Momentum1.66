#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2026-03-05
@File       : tracker.py
@Description: 诊断型科考船 - 支持动态异常度记录与筹码地形分析
"""
import csv
import datetime
import os
import time

from src.utils.log import get_logger

logger = get_logger(__name__)


class CSVTracker:
    def __init__(self, project_root, context=None):
        self.active_trackings = []
        self.csv_file = os.path.join(project_root, "data", "bounce_records.csv")
        self.context = context  # MarketContext实例，用于获取实时价格和时间戳

        os.makedirs(os.path.dirname(self.csv_file), exist_ok=True)
        # 🌟 重新设计表头：引入异常度诊断维度
        if not os.path.exists(self.csv_file):
            with open(self.csv_file, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    '触发时间', '信号等级', '砸盘(百万)', '反转(百万)', '入场反弹(%)',
                    '资金异常(倍)', '阻力异常(倍)', '触发价', '最高价', '最大反弹(%)',
                    '耗时(秒)', '筹码地形', '结束原因'
                ])

    def add_tracking(self, signal: dict):
        """记录所有维度，方便复盘"""
        self.active_trackings.append({
            'entry_time': signal['ts'],
            'level': signal.get('level', 'UNKNOWN'),
            'entry_price': signal['price'],
            'cvd_delta_usdt': signal.get('cvd_delta_usdt', 0),
            'micro_cvd': signal.get('micro_cvd', 0),           # 🌟 物理反转
            'entry_bounce': signal.get('price_diff_pct', 0),   # 🌟 物理入场位
            'effort_anomaly': signal.get('effort_anomaly', 0), # 🌟 诊断倍数
            'res_anomaly': signal.get('res_anomaly', 0),       # 🌟 诊断阻力
            'terrain': signal.get('smc_msg', 'N/A'),           # 🌟 地形标签
            'max_price': signal['price'],
            'local_low': signal.get('local_low', signal['price']),
            'last_update': signal['ts']
        })
        logger.info(f"📊 [科考船] 捕获 {signal['level']} 级别信号，地形: {signal.get('smc_msg', '未知')}，砸盘：{round(signal.get('cvd_delta_usdt', 0)/1000000, 2)}百万，反转：{round(signal.get('micro_cvd', 0)/1000000, 2)}百万")

    def update_trackings(self):
        """更新所有追踪单的最大反弹值，并检查止损（从MarketContext获取实时数据）"""
        if not self.context:
            logger.warning("⚠️ [科考船] 未提供MarketContext，无法更新追踪")
            return

        # 从MarketContext获取当前价格和时间戳
        current_price = self.context.get_current_price()
        current_ts = self.context.get_last_tick_ts()

        if current_price <= 0 or current_ts <= 0:
            logger.warning(f"⚠️ [科考船] 从MarketContext获取的数据无效: price={current_price}, ts={current_ts}")
            return

        remaining = []
        for track in self.active_trackings:
            track['max_price'] = max(track['max_price'], current_price)
            track['last_update'] = current_ts
            sl_price = track['local_low'] * 0.9985

            # 1. 破位止损 (跌破了触发时的坑底价)
            if current_price < sl_price:
                self._write_to_csv(track, current_ts, "破位止损")
            # 2. 时间到了 (30分钟自动归档)
            elif current_ts - track['entry_time'] > 1800:
                self._write_to_csv(track, current_ts, "时间到了(1h)")
            else:
                remaining.append(track)

        self.active_trackings = remaining

    def force_close_all(self):
        """安全迫降：程序退出时强制结算"""
        if not self.active_trackings: return
        current_ts = time.time()
        for track in self.active_trackings:
            self._write_to_csv(track, current_ts, "程序重启中断")
        self.active_trackings.clear()

    def _write_to_csv(self, track, current_ts, end_reason):
        bounce_pct = (track['max_price'] - track['entry_price']) / track['entry_price'] * 100
        duration = current_ts - track['entry_time']
        effort_m = abs(track['cvd_delta_usdt']) / 1_000_000
        rebound_m = track['micro_cvd'] / 1_000_000

        try:
            with open(self.csv_file, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.datetime.fromtimestamp(track['entry_time']).strftime('%H:%M:%S'),
                    track['level'],
                    round(effort_m, 2),
                    round(rebound_m, 2),              # 物理反转量
                    round(track['entry_bounce'], 3),  # 入场时的偏离
                    round(track['effort_anomaly'], 2), # 异常倍数
                    round(track['res_anomaly'], 2),    # 阻力倍数
                    track['entry_price'],
                    track['max_price'],
                    round(bounce_pct, 4),
                    round(duration, 1),
                    track['terrain'],
                    end_reason
                ])
        except Exception as e:
            logger.error(f"❌ 记录 CSV 失败: {e}")
