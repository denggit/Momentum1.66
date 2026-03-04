# src/strategy/orderflow.py
import time
from collections import deque


class OrderFlowMath:
    def __init__(self):
        self.cvd = 0.0
        self.current_price = 0.0
        self.snapshots = deque(maxlen=300)
        self.last_snapshot_time = time.time()

        # 冷却锁与价格破局记忆
        self.last_broad_trigger_time = 0
        self.last_broad_trigger_price = 0.0
        self.last_strict_trigger_time = 0
        self.last_strict_trigger_price = 0.0

    def process_tick(self, tick: dict):
        """处理逐笔交易，更新CVD。每10秒计算一次信号"""
        self.current_price = tick['price']
        size = tick['size']
        side = tick['side']
        current_ts = tick['ts']

        # CVD 核心累计
        if side == 'buy':
            self.cvd += size
        else:
            self.cvd -= size

        # 每 10 秒拍一次快照并检测背离
        if current_ts - self.last_snapshot_time >= 10:
            self.snapshots.append({
                'ts': current_ts,
                'price': self.current_price,
                'cvd': self.cvd
            })
            self.last_snapshot_time = current_ts
            return self._detect_absorption(current_ts)

        return None

    def _detect_absorption(self, current_ts):
        """核心检测算法：返回 BROAD（宽口径）或 STRICT（严口径）信号字典"""
        if len(self.snapshots) < 90:  # 冷启动防御 15 分钟
            return None

        LOOKBACK_WINDOW = 90
        past_snapshots = list(self.snapshots)[-LOOKBACK_WINDOW:-1]
        lowest_snap = min(past_snapshots, key=lambda x: x['price'])
        current_snap = self.snapshots[-1]

        # 🌟 升级为跨币种通用的百分比算法：
        price_diff_pct = (current_snap['price'] - lowest_snap['price']) / lowest_snap['price'] * 100

        RECENT_WINDOW = 18  # 180秒
        snapshot_3min_ago = self.snapshots[-RECENT_WINDOW]
        recent_cvd_delta_contracts = current_snap['cvd'] - snapshot_3min_ago['cvd']
        CONTRACT_SIZE = 0.1
        recent_cvd_delta_usdt = recent_cvd_delta_contracts * CONTRACT_SIZE * current_snap['price']

        last_snap = self.snapshots[-2]
        micro_cvd_delta_contracts = current_snap['cvd'] - last_snap['cvd']
        micro_cvd_delta_usdt = micro_cvd_delta_contracts * CONTRACT_SIZE * current_snap['price']

        time_passed = (current_snap['ts'] - lowest_snap['ts']) > 20
        if not time_passed:
            return None

        # ==========================================
        # ⚔️ 内层网：实盘严口径 (STRICT)
        # ==========================================
        # 只要从最低点反弹不超过 0.2%，就不算追高！
        strict_price_ok = price_diff_pct <= 0.20
        strict_cvd_ok = recent_cvd_delta_usdt < -1_500_000  # -500万巨量砸盘
        strict_turn_ok = micro_cvd_delta_usdt > 100_000  # 15万买盘反抽

        strict_time_ok = (current_ts - self.last_strict_trigger_time) > 300
        strict_price_override = current_snap['price'] < (self.last_strict_trigger_price - 3.0)

        if strict_price_ok and strict_cvd_ok and strict_turn_ok and (strict_time_ok or strict_price_override):
            self.last_strict_trigger_time = current_ts
            self.last_strict_trigger_price = current_snap['price']
            return {
                "level": "STRICT",
                "price": current_snap['price'],
                "cvd_delta_usdt": recent_cvd_delta_usdt,
                "micro_cvd": micro_cvd_delta_usdt,
                "price_diff_pct": price_diff_pct,
                "ts": current_ts
            }

        # ==========================================
        # 🧪 外层网：科考船宽口径 (BROAD)
        # ==========================================
        # 宽口径容忍度放宽到 0.3%
        broad_price_ok = price_diff_pct <= 0.30
        broad_cvd_ok = recent_cvd_delta_usdt < -1_000_000  # -200万砸盘
        broad_turn_ok = micro_cvd_delta_usdt > 30_000  # 5万买盘反抽

        broad_time_ok = (current_ts - self.last_broad_trigger_time) > 300
        broad_price_override = current_snap['price'] < (self.last_broad_trigger_price - 2.0)

        if broad_price_ok and broad_cvd_ok and broad_turn_ok and (broad_time_ok or broad_price_override):
            self.last_broad_trigger_time = current_ts
            self.last_broad_trigger_price = current_snap['price']
            return {
                "level": "BROAD",
                "price": current_snap['price'],
                "cvd_delta_usdt": recent_cvd_delta_usdt,
                "micro_cvd": micro_cvd_delta_usdt,
                "price_diff_pct": price_diff_pct,
                "ts": current_ts
            }

        return None