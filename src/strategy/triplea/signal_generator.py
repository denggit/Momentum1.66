from collections import deque
from typing import Dict, Optional

from src.utils.log import get_logger

logger = get_logger(__name__)


class FabioTickSignalGenerator:
    def __init__(self, symbol: str = "ETH-USDT-SWAP"):
        self.symbol = symbol
        self.status = "IDLE"
        self.tradable_zones = []
        self.target_zone = None
        self.radar_expansion = 0.5  # 引力光晕倍数
        self.profile = {}  # [修复] 补齐 profile 定义

        # ==========================================
        # 🆕 全天候微观数据收集器
        # ==========================================
        self.rolling_ticks = deque()
        self.rolling_window_sec = 15.0
        self.global_cvd = 0.0
        self.global_volume = 0.0

        # ==========================================
        # 📸 订单生命周期内存 (快照冻结)
        # ==========================================
        self.current_sl = 0.0  # [修复] 补齐绝对止损价定义
        self.current_tp = 0.0  # [修复] 补齐绝对止盈价定义

        # 订单流微观追踪器
        self.micro_tracker = {
            "absorption_price": 0.0,
            "micro_resistance": 0.0,
            "a2_start_time": 0.0
        }

    def _update_rolling_data(self, price: float, size: float, side: str, current_time: float):
        """维护一个永远反映过去 15 秒盘口真实情况的滑动窗口"""
        tick_delta = size if side == 'buy' else -size
        self.rolling_ticks.append((current_time, tick_delta, size, price))
        self.global_cvd += tick_delta
        self.global_volume += size

        # 剔除过期数据，保持 15 秒窗口
        while self.rolling_ticks and current_time - self.rolling_ticks[0][0] > self.rolling_window_sec:
            old_time, old_delta, old_size, old_price = self.rolling_ticks.popleft()
            self.global_cvd -= old_delta
            self.global_volume -= old_size

    def update_macro_map(self, profile_data: Dict):
        """慢速接口：在这里把沉重的计算提前做完！"""
        self.tradable_zones = profile_data.get('tradable_zones', [])
        self.profile = profile_data

        for zone in self.tradable_zones:
            if "MEGA" not in zone['type']:
                zone_width = zone['zone_high'] - zone['zone_low']
                zone['halo_high'] = zone['zone_high'] + (zone_width * self.radar_expansion)
                zone['halo_low'] = zone['zone_low'] - (zone_width * self.radar_expansion)

    def process_tick(self, tick: Dict) -> Optional[Dict]:
        if self.status in ["LONG", "SHORT"]:
            return self._manage_position_by_tick(tick)

        price = tick['price']
        size = tick['size']
        current_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0

        # 1. 极速队列更新
        self._update_rolling_data(price, size, tick['side'], current_time)

        # --------------------------------------------------
        # 阶段 0: 寻找交火区
        # --------------------------------------------------
        if self.status == "IDLE":
            for zone in self.tradable_zones:
                if "MEGA" in zone['type']:
                    continue
                if zone['halo_low'] <= price <= zone['halo_high']:
                    self.status = "A1_WAIT_ABSORPTION"
                    self.target_zone = zone
                    return None

        # --------------------------------------------------
        # 第一重 A: Absorption
        # --------------------------------------------------
        elif self.status == "A1_WAIT_ABSORPTION":
            if price < self.target_zone['halo_low']:
                self._reset_to_idle()
                return None

            if not self.rolling_ticks:
                return None

            # 1. 评判空头的“努力 (Effort)” -> 依然使用 15 秒的全局数据！
            baseline_15s_vol = self.profile.get('avg_vol_1m', 100) / 4.0
            is_volume_spike = self.global_volume > (baseline_15s_vol * 2.5)

            cvd_intensity = abs(self.global_cvd) / (self.global_volume + 1)
            is_heavy_selling = self.global_cvd < 0 and cvd_intensity > 0.3

            # 只有在 15秒级别确实发生爆量砸盘时，才去检查最近几秒是不是“刹车”了
            if is_volume_spike and is_heavy_selling:

                # 2. 评判空头的“结果 (Result)” -> 【核心进化】只切片看最近 3 秒的刹车情况！
                micro_time_threshold = current_time - 3.0

                # 从 15 秒的队列里，过滤出最近 3 秒的 Tick
                micro_ticks = [t for t in self.rolling_ticks if t[0] >= micro_time_threshold]

                if micro_ticks:
                    # 只算这最近 3 秒的最高价和最低价
                    recent_low = min(t[3] for t in micro_ticks)
                    recent_high = max(t[3] for t in micro_ticks)

                    safe_low = max(recent_low, 1e-8)
                    price_range_pct = (recent_high - safe_low) / safe_low

                    # 如果在最近的 3 秒内，价格被死死压缩在 0.05% 以内，说明刹车成功！
                    if price_range_pct <= 0.0005:
                        self.status = "A2_WAIT_ACCUMULATION"
                        # 底线依然用这 3 秒内探出的最低点
                        self.micro_tracker['absorption_price'] = recent_low
                        self.micro_tracker['micro_resistance'] = recent_high
                        self.micro_tracker['a2_start_time'] = current_time

                        logger.info(
                            f"🧱 [A1-吸收] 15秒天量砸盘 ({self.global_volume:.2f})！但最近3秒被死死卡在 {price_range_pct:.4%} 内！刹车成功！")
                        return None

        # --------------------------------------------------
        # 第二重 A: Accumulation
        # --------------------------------------------------
        elif self.status == "A2_WAIT_ACCUMULATION":
            if price < self.micro_tracker['absorption_price']:
                logger.debug("💥 吸收底线被击穿，多头防线崩溃，撤退！")
                self._reset_to_idle()
                return None

            self.micro_tracker['micro_resistance'] = max(self.micro_tracker['micro_resistance'], price)

            if current_time - self.micro_tracker['a2_start_time'] > 5.0:
                self.status = "A3_WAIT_AGGRESSION"
                logger.info(f"🔋 [A2-积累] 筹码换手完成，阻力线: {self.micro_tracker['micro_resistance']}。")
                return None

        # --------------------------------------------------
        # 第三重 A: Aggression (拔枪开火)
        # --------------------------------------------------
        elif self.status == "A3_WAIT_AGGRESSION":
            if price < self.micro_tracker['absorption_price']:
                self._reset_to_idle()
                return None

            if price > self.micro_tracker['micro_resistance'] and self.global_cvd > (self.global_volume * 0.15):
                logger.info(f"⚔️ [A3-攻击达成] 多头放量突破积累区！全军出击做多！")

                # [修复] 极其致命！计算出 SL 和 TP，并赋值给内存，修改机器人的全局状态！
                sl = self.micro_tracker['absorption_price'] - 1.0
                tp = self.profile['POC']['center']

                self.current_sl = sl
                self.current_tp = tp
                self.status = "LONG"  # 锁定状态，下个 Tick 直接移交给 _manage_position_by_tick！

                return {
                    "action": "BUY",
                    "entry_price": price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "reason": "TRIPLE_A_COMPLETE"
                }

        return None

    def _reset_to_idle(self):
        self.status = "IDLE"
        self.target_zone = None
        # 清理微观追踪器状态
        self.micro_tracker = {
            "absorption_price": 0.0,
            "micro_resistance": 0.0,
            "a2_start_time": 0.0
        }

    def _manage_position_by_tick(self, tick: Dict) -> Optional[Dict]:
        """
        持仓飞行模式 (IN_POSITION)：
        引擎进入自动驾驶状态，拿着每一笔最新成交价去撞击死命令 (SL / TP)。
        """
        price = tick['price']
        signal = None

        if self.status == "LONG":
            if price <= self.current_sl:
                signal = {"action": "CLOSE_LONG", "reason": "STOP_LOSS_HIT", "price": price}
            elif price >= self.current_tp:
                signal = {"action": "CLOSE_LONG", "reason": "TAKE_PROFIT_HIT", "price": price}

        elif self.status == "SHORT":
            if price >= self.current_sl:
                signal = {"action": "CLOSE_SHORT", "reason": "STOP_LOSS_HIT", "price": price}
            elif price <= self.current_tp:
                signal = {"action": "CLOSE_SHORT", "reason": "TAKE_PROFIT_HIT", "price": price}

        if signal:
            logger.info(f"🏁 订单终结！触发原因: {signal['reason']}，成交价: {price}。")
            self._reset_to_idle()
            self.current_sl = 0.0
            self.current_tp = 0.0

        return signal
