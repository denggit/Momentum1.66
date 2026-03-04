#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26 8:53 PM
@File       : tracker.py
@Description: 
"""
import csv
import datetime
import os
import time
from src.utils.log import get_logger

logger = get_logger(__name__)


class CSVTracker:
    def __init__(self, project_root):
        self.active_trackings = []
        self.csv_file = os.path.join(project_root, "data", "bounce_records.csv")

        os.makedirs(os.path.dirname(self.csv_file), exist_ok=True)
        if not os.path.exists(self.csv_file):
            with open(self.csv_file, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                # 包含 9 列的新表头（加入了反转量）
                writer.writerow(
                    ['触发时间', 'CVD砸盘量(万刀)', 'CVD反转量(万刀)', '偏离前低(刀)', '触发价格', '反弹最高价',
                     '最大反弹幅度(%)', '追踪耗时(秒)', '结束原因'])

    def add_tracking(self, signal: dict):
        """将新信号加入追踪队列"""
        self.active_trackings.append({
            'entry_time': signal['ts'],
            'entry_price': signal['price'],
            'cvd_delta_usdt': signal['cvd_delta_usdt'],
            'micro_cvd_delta_usdt': signal['micro_cvd'],
            'price_diff_pct': signal['price_diff_pct'],
            'max_price': signal['price']
        })
        logger.info(f"📊 已将信号加入科考船追踪队列，当前追踪任务数: {len(self.active_trackings)}")

    def update_trackings(self, current_price, current_ts):
        """动态更新高点，并判定是否结束"""
        for track in self.active_trackings[:]:
            if current_price > track['max_price']:
                track['max_price'] = current_price

            end_reason = None
            if current_price < (track['entry_price'] - 3.0):
                end_reason = "破位止损"
            elif current_ts - track['entry_time'] > 900:
                end_reason = "时间到了(15分钟)"

            if end_reason:
                self._write_to_csv(track, current_ts, end_reason)
                self.active_trackings.remove(track)

    def force_close_all(self):
        """安全迫降：kill -15 时强制结算所有内存订单"""
        if not self.active_trackings:
            return
        current_ts = time.time()
        for track in self.active_trackings:
            self._write_to_csv(track, current_ts, "程序重启中断(强制结算)")
        logger.info(f"✅ 完美！已将 {len(self.active_trackings)} 个未完成的追踪记录抢救至 CSV！")
        self.active_trackings.clear()

    def _write_to_csv(self, track, current_ts, end_reason):
        bounce_pct = (track['max_price'] - track['entry_price']) / track['entry_price'] * 100
        duration = current_ts - track['entry_time']
        try:
            with open(self.csv_file, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.datetime.fromtimestamp(track['entry_time']).strftime('%Y-%m-%d %H:%M:%S'),
                    round(track['cvd_delta_usdt'] / 10000, 2),
                    round(track['micro_cvd_delta_usdt'] / 10000, 2),
                    round(track['price_diff_pct'], 2),
                    track['entry_price'],
                    track['max_price'],
                    round(bounce_pct, 4),
                    round(duration, 1),
                    end_reason
                ])
            if "中断" not in end_reason:
                logger.info(
                    f"📊 归档 [{end_reason}] -> CVD: {track['cvd_delta_usdt'] / 10000:.1f}万 | 反弹: {bounce_pct:.3f}%")
        except Exception as e:
            logger.error(f"CSV写入失败: {e}")