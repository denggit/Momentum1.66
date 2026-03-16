#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/15/26 4:34 PM
@File       : trajectory_miner.py
@Description: 轨迹矿工 - 异步记录成功突破(WIN)和假突破止损(LOSS)前15分钟的完整逐秒底层指标轨迹
"""

import asyncio
import copy
import csv
import os
import time
from collections import deque
from typing import Dict, List

from src.utils.log import get_logger

logger = get_logger(__name__)


class TrajectoryMiner:
    """轨迹矿工 - 异步记录成功突破(WIN)和假突破止损(LOSS)前15分钟的完整逐秒底层指标轨迹"""

    def __init__(self, symbol: str = "ETH-USDT-SWAP"):
        self.symbol = symbol

        # 全局录像带：固定长度1800（30分钟 * 60秒），每秒钟无脑推入一次底层快照
        self.rolling_tape = deque(maxlen=1800)  # 每个元素是字典格式

        # 幽灵追踪器：{tracker_id: tracker_data}
        self.active_trackers: Dict[str, Dict] = {}

        # 冷却字典：防止同一个框在震荡市被反复追踪 {zone_key: expiry_timestamp}
        self.cooldowns: Dict[str, float] = {}

        # 输出目录
        self.output_dir = "data/tripleA/miner"
        os.makedirs(self.output_dir, exist_ok=True)

        # 追踪器ID计数器
        self._tracker_counter = 0

        # V2.0 新增参数 - 基于轨迹矿工分析优化
        # 🚨 修正：矿工应该使用宽松参数收集数据，实盘才用严格参数
        self.spike_multiplier = 1.5  # 矿工A3爆量触发门槛（宽松参数，用于数据收集）
        self.zone_margin = 5.0  # 允许出框的判定容差 (点数)

        # 🆕 V2.1 新增：矿工专用A1检测参数（宽松版本）
        self.a1_spike_threshold = 1.3  # A1成交量突增倍数（实盘用1.8，矿工用宽松1.3）
        self.a1_delta_ratio_threshold = 0.20  # A1净买卖比门槛（实盘用0.30，矿工用0.20）
        self.a1_price_stability_pct = 0.002  # A1价格稳定要求（0.2%振幅）
        self.a1_min_duration = 1.0  # A1最小持续时间（秒，实盘用3.0，矿工用1.0）
        self.a1_cooldown_sec = 30  # A1记录冷却时间，防止重复记录

        logger.info(f"🚀 轨迹矿工V2.0初始化完成: {symbol}")

    def update_tape_tick(self, price: float, cvd: float, vol: float, delta_ratio: float):
        """
        每秒钟调用一次，更新全局录像带
        :param price: 当前价格
        :param cvd: 15秒窗口的全局CVD
        :param vol: 15秒窗口的全局成交量
        :param delta_ratio: 净买卖比 (abs(cvd)/vol)
        """
        try:
            snapshot = {
                "timestamp": time.time(),
                "price": price,
                "cvd": cvd,
                "vol": vol,
                "delta_ratio": delta_ratio
            }
            self.rolling_tape.append(snapshot)
        except Exception as e:
            logger.error(f"❌ 更新录像带异常: {e}")

    def check_a1_pattern(self, price: float, cvd: float, vol: float, delta_ratio: float,
                         baseline_vol: float, tradable_zones: List[Dict]):
        """
        A1吸收形态检测（矿工宽松版本）
        目标：收集所有疑似A1的形态，包括失败的案例
        """
        try:
            # 1. 冷却检查
            current_time = time.time()
            if hasattr(self, '_last_a1_record_time'):
                if current_time - self._last_a1_record_time < self.a1_cooldown_sec:
                    return False

            # 2. 成交量突增检查（宽松条件）
            if vol < baseline_vol * self.a1_spike_threshold:
                return False

            # 3. 净买卖比检查（宽松条件）
            if abs(delta_ratio) < self.a1_delta_ratio_threshold:
                return False

            # 4. 价格稳定性检查（使用最近15秒数据）
            if len(self.rolling_tape) < 15:
                return False

            recent_prices = [snapshot['price'] for snapshot in list(self.rolling_tape)[-15:]]
            price_range = max(recent_prices) - min(recent_prices)
            avg_price = sum(recent_prices) / len(recent_prices)
            price_stability = price_range / (avg_price + 1e-8)

            if price_stability > self.a1_price_stability_pct:
                return False  # 价格波动太大，不是A1吸收

            # 5. 区域检查（是否在交易区域内）
            in_zone = False
            for zone in tradable_zones:
                if "MEGA" in zone.get('type', '') or zone.get('type') == "POC":
                    continue
                zone_low = zone.get('zone_low')
                zone_high = zone.get('zone_high')
                if zone_low is None or zone_high is None:
                    continue
                if zone_low <= price <= zone_high:
                    in_zone = True
                    break

            if not in_zone:
                return False  # 不在交易区域内

            # 6. 记录A1疑似形态
            self._record_a1_suspect(price, cvd, vol, delta_ratio, recent_prices)
            self._last_a1_record_time = current_time
            return True

        except Exception as e:
            logger.error(f"❌ A1形态检测异常: {e}")
            return False

    def _record_a1_suspect(self, price: float, cvd: float, vol: float, delta_ratio: float,
                          recent_prices: List[float]):
        """记录A1疑似形态到文件"""
        try:
            a1_dir = os.path.join(self.output_dir, "a1_suspects")
            os.makedirs(a1_dir, exist_ok=True)

            timestamp = int(time.time())
            filename = f"A1_SUSPECT_{timestamp}_{price:.2f}.json"
            filepath = os.path.join(a1_dir, filename)

            a1_data = {
                "timestamp": timestamp,
                "price": price,
                "cvd": cvd,
                "vol": vol,
                "delta_ratio": delta_ratio,
                "price_range": max(recent_prices) - min(recent_prices),
                "price_avg": sum(recent_prices) / len(recent_prices),
                "recent_prices": recent_prices[-10:],  # 只保存最近10个价格
                "rolling_tape_length": len(self.rolling_tape)
            }

            import json
            with open(filepath, 'w') as f:
                json.dump(a1_data, f, indent=2)

            logger.debug(f"📝 记录A1疑似形态: {price:.2f}, CVD={cvd:.0f}, Vol={vol:.0f}")

        except Exception as e:
            logger.error(f"❌ 记录A1疑似形态失败: {e}")

    def check_and_spawn_tracker(self, price: float, cvd: float, vol: float, baseline_vol: float,
                                tradable_zones: List[Dict]):
        """
        A3 狙击雷达：爆量触发 -> 战区定位 -> 现场倒查 -> 瞬间锁存
        """
        try:
            # ==========================================
            # 🔍 0. A1吸收形态检测（矿工宽松版本，独立于A3）
            # ==========================================
            delta_ratio = abs(cvd) / (vol + 1e-8)
            self.check_a1_pattern(price, cvd, vol, delta_ratio, baseline_vol, tradable_zones)

            # ==========================================
            # 🛡️ 1. 异动扳机 (A3 点火检测)
            # ==========================================
            if vol < baseline_vol * self.spike_multiplier:
                return  # 没爆量，直接睡大觉

            direction = "LONG" if cvd > 0 else "SHORT"

            # ==========================================
            # 🗺️ 2. 战场定位 (必须在框内，或刚出框的边缘)
            # ==========================================
            target_zone = None
            for zone in tradable_zones:
                if "MEGA" in zone.get('type', '') or zone.get('type') == "POC":
                    continue
                zone_low = zone.get('zone_low')
                zone_high = zone.get('zone_high')
                if zone_low is None or zone_high is None:
                    continue

                # 判定容差：允许在框内，也允许刚刚突破出框
                if (zone_low - self.zone_margin) <= price <= (zone_high + self.zone_margin):
                    target_zone = zone
                    break

            if not target_zone:
                return  # 爆量发生在半空中的垃圾时间，无视

            zone_key = f"{target_zone.get('type', 'UNKNOWN')}_{target_zone.get('zone_low'): .2f}_{target_zone.get('zone_high'): .2f}"

            # 检查冷却 (防抖：避免连续爆量导致重复录像)
            if zone_key in self.cooldowns:
                if time.time() < self.cooldowns[zone_key]:
                    return
                else:
                    del self.cooldowns[zone_key]

            # ==========================================
            # 🔍 3. 倒查犯罪现场 (寻找 A1 吸收深坑)
            # ==========================================
            if len(self.rolling_tape) < 60:
                return  # 录像带太短，不足以形成坑，放弃

            prices_in_tape = [snapshot['price'] for snapshot in self.rolling_tape]
            swing_low = min(prices_in_tape)
            swing_high = max(prices_in_tape)

            tp_price = None
            sl_price = None

            # 🚀 V2.1 新增：强制底线利润空间 (防止吃不到肉的垃圾记录)
            # 1. 最小盈亏比：必须大于止损风险的 1.5 倍
            # 2. 最小绝对空间：硬性规定利润空间不能低于现价的 0.5% (比如 2100刀必须有至少 10.5 刀的利润)
            min_rr = 1.5
            min_pct_profit = 0.005
            min_absolute_profit = price * min_pct_profit

            if direction == "LONG":
                # 做多条件 1：过去30分钟内必须有一个明显的深坑 (至少低于当前价 3 刀)
                if price - swing_low < 3.0:
                    return  # 没有深坑，说明是高位追涨，无视

                # 🚀 V2.2 新增约束：绝对对齐实盘空间！最低点不能跌穿当前支撑框的下沿超过 5 刀
                if swing_low < target_zone.get('zone_low') - 5.0:
                    return  # 跌穿宏观阵地太深，说明主力防线根本不在这里，拒录！

                sl_price = swing_low  # 止损直接设为刚刚查到的深坑底部！
                risk = price - sl_price

                # 计算这单的“强制底线 TP”位置
                required_profit = max(risk * min_rr, min_absolute_profit)
                min_tp_target = price + required_profit

                # 向上寻址找 TP
                candidate_zones = [z for z in tradable_zones
                                   if z.get('zone_low', 0) >= min_tp_target
                                   and "MEGA" not in z.get('type', '')]
                if candidate_zones:
                    next_zone = min(candidate_zones, key=lambda z: z.get('center') - price)
                    tp_price = next_zone.get('zone_low')
                else:
                    tp_price = min_tp_target

            else:  # SHORT
                # 做空条件 1：过去30分钟内必须有一个明显的尖峰 (至少高于当前价 3 刀)
                if swing_high - price < 3.0:
                    return

                # 🚀 V2.2 新增约束：绝对对齐实盘空间！最高点不能涨破当前阻力框的上沿超过 5 刀
                if swing_high > target_zone.get('zone_high') + 5.0:
                    return  # 涨破宏观阵地太高，说明主力防线根本不在这里，拒录！

                sl_price = swing_high  # 止损直接设为尖峰顶部！
                risk = sl_price - price

                # 计算这单的“强制底线 TP”位置
                required_profit = max(risk * min_rr, min_absolute_profit)
                min_tp_target = price - required_profit

                # 向下寻址找 TP
                candidate_zones = [z for z in tradable_zones
                                   if z.get('zone_high', float('inf')) <= min_tp_target
                                   and "MEGA" not in z.get('type', '')]
                if candidate_zones:
                    next_zone = min(candidate_zones, key=lambda z: price - z.get('center'))
                    tp_price = next_zone.get('zone_high')
                else:
                    tp_price = min_tp_target

            # ==========================================
            # 📸 4. 瞬间锁存与挂机 (最核心动作)
            # ==========================================
            tracker_id = self._generate_tracker_id()

            # 🚨 极度关键：在 A3 爆量的这一瞬间，立刻拔出 U 盘锁死！
            frozen_tape = copy.deepcopy(list(self.rolling_tape))

            tracker_data = {
                "tracker_id": tracker_id,
                "zone_key": zone_key,
                "entry_price": price,
                "entry_time": time.time(),
                "tp_price": tp_price,
                "sl_price": sl_price,
                "direction": direction,
                "frozen_tape": frozen_tape,  # 👈 随身携带锁死的起涨点录像带
                "mfe_price": price,  # 最大有利价格
                "mae_price": price  # 最大不利价格
            }

            self.active_trackers[tracker_id] = tracker_data

            # 立刻加入 5 分钟冷却，防止接下来几分钟的爆量重复触发
            self.cooldowns[zone_key] = time.time() + 300

            logger.info(
                f"📸 抓获 A3 爆量起涨点! {direction} @ {price: .2f} | 倒查 Swing: {swing_low if direction == 'LONG' else swing_high: .2f} | TP={tp_price: .2f}, SL={sl_price: .2f}")

        except Exception as e:
            logger.error(f"❌ A3雷达扫描异常: {e}")

    def evaluate_trackers(self, price: float):
        """
        结算器：只看现价是否撞线，撞线就带着之前的锁死录像带去写 CSV
        """
        try:
            completed_trackers = []

            for tracker_id, tracker in list(self.active_trackers.items()):
                tp_price = tracker["tp_price"]
                sl_price = tracker["sl_price"]
                direction = tracker["direction"]

                # 🚀 实时更新 MFE 和 MAE
                if tracker["direction"] == "LONG":
                    tracker["mfe_price"] = max(tracker["mfe_price"], price)
                    tracker["mae_price"] = min(tracker["mae_price"], price)
                else:
                    tracker["mfe_price"] = min(tracker["mfe_price"], price)
                    tracker["mae_price"] = max(tracker["mae_price"], price)

                hit_tp = (price >= tp_price) if direction == "LONG" else (price <= tp_price)
                hit_sl = (price <= sl_price) if direction == "LONG" else (price >= sl_price)

                if hit_tp or hit_sl:
                    result_type = "WIN" if hit_tp else "LOSS"
                    entry_price = tracker["entry_price"]

                    # 算盈亏百分比
                    if direction == "LONG":
                        pnl_pct = (price - entry_price) / entry_price * 100
                    else:
                        pnl_pct = (entry_price - price) / entry_price * 100

                    duration_sec = int(time.time() - tracker["entry_time"])

                    # 计算 MFE 和 MAE 距离
                    if direction == "LONG":
                        mae_dist = tracker["entry_price"] - tracker["mae_price"]
                        mfe_dist = tracker["mfe_price"] - tracker["entry_price"]
                    else:
                        mae_dist = tracker["mae_price"] - tracker["entry_price"]
                        mfe_dist = tracker["entry_price"] - tracker["mfe_price"]

                    settlement_info = {
                        "tracker_id": tracker_id,
                        "result_type": result_type,
                        "settlement_price": price,
                        "settlement_time": time.time(),
                        "pnl_pct": pnl_pct,
                        "duration_sec": duration_sec,
                        "tape_data": tracker["frozen_tape"],  # 👈 写入的是当时锁死的起涨点录像带！
                        "tracker_data": copy.deepcopy(tracker),
                        "mae_distance": mae_dist,  # 👈 塞入最大不利回撤距离
                        "mfe_distance": mfe_dist  # 👈 塞入最大有利距离
                    }

                    completed_trackers.append(settlement_info)
                    del self.active_trackers[tracker_id]
                    logger.info(
                        f"🎯 录像带结算: {result_type} @ {price: .2f} (耗时: {duration_sec}s, 盈亏: {pnl_pct: .2f}%)")

            for settlement in completed_trackers:
                asyncio.create_task(self._dump_to_csv_async(settlement))

        except Exception as e:
            logger.error(f"❌ 结算异常: {e}")

    async def _dump_to_csv_async(self, settlement: Dict):
        try:
            tracker_id = settlement["tracker_id"]
            result_type = settlement["result_type"]
            settlement_time = settlement["settlement_time"]
            tape_data = settlement["tape_data"]

            # 🚀 新增：提取盈亏和耗时，格式化为字符串
            pnl_pct = settlement["pnl_pct"]
            duration = settlement["duration_sec"]

            # 例如: P1.25 (赚1.25%) 或者 L0.45 (亏0.45%)
            pnl_str = f"P{pnl_pct: .2f}" if pnl_pct >= 0 else f"L{abs(pnl_pct): .2f}"

            # 生成超强可读性的文件名！
            # 格式例: WIN_ETH-USDT-SWAP_20260315_143000_P1.25_245s_TR_123.csv
            timestamp_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(settlement_time))
            filename = f"{result_type}_{self.symbol}_{timestamp_str}_{pnl_str}_{duration}s_{tracker_id}.csv"

            filepath = os.path.join(self.output_dir, filename)

            # 准备CSV行数据
            rows = []
            for i, snapshot in enumerate(tape_data):
                # 计算T_minus（距离结算时刻倒退的秒数）
                t_minus = - (len(tape_data) - 1 - i)  # 从-(len-1)到0 (例如 -1799 到 0)

                row = [
                    snapshot["timestamp"],
                    t_minus,
                    snapshot["price"],
                    snapshot["cvd"],
                    snapshot["vol"],
                    snapshot["delta_ratio"]
                ]
                rows.append(row)

            # 使用线程池异步写入文件（避免阻塞主循环）
            def write_to_file():
                with open(filepath, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Timestamp", "T_minus", "Price", "CVD_15s", "Volume_15s", "Delta_Ratio"])
                    for r in rows:
                        writer.writerow(r)

            await asyncio.to_thread(write_to_file)
            logger.debug(f"💾 轨迹数据已写入: {filename} ({len(rows)}行)")

        except Exception as e:
            logger.error(f"❌ 异步写入CSV异常: {e}")

    def _generate_tracker_id(self) -> str:
        """生成唯一的追踪器ID"""
        self._tracker_counter += 1
        timestamp = int(time.time() * 1000) % 1000000
        return f"TR_{timestamp}_{self._tracker_counter}"

    def cleanup_expired_cooldowns(self):
        """清理过期的冷却条目"""
        current_time = time.time()
        expired_keys = [k for k, v in self.cooldowns.items() if v < current_time]
        for key in expired_keys:
            del self.cooldowns[key]
        if expired_keys:
            logger.debug(f"🧹 清理了 {len(expired_keys)} 个过期的冷却条目")
