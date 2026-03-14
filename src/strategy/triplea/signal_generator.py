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
        self.global_boxes = {}

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
        """O(1) 极速更新：新数据加进来，老数据踢出去"""
        tick_delta = size if side == 'buy' else -size
        self.rolling_ticks.append((current_time, tick_delta, size, price))

        self.global_cvd += tick_delta
        self.global_volume += size

        # 🆕 进场：把新 Tick 累加进 1U 箱子
        box_id = int(price)
        if box_id not in self.global_boxes:
            self.global_boxes[box_id] = {'volume': 0.0, 'delta': 0.0}
        self.global_boxes[box_id]['volume'] += size
        self.global_boxes[box_id]['delta'] += tick_delta

        # 🆕 退场：剔除过期数据，保持 15 秒窗口，同时把箱子里的量扣掉！
        while self.rolling_ticks and current_time - self.rolling_ticks[0][0] > self.rolling_window_sec:
            old_time, old_delta, old_size, old_price = self.rolling_ticks.popleft()
            self.global_cvd -= old_delta
            self.global_volume -= old_size

            old_box_id = int(old_price)
            self.global_boxes[old_box_id]['volume'] -= old_size
            self.global_boxes[old_box_id]['delta'] -= old_delta

            # 如果箱子空了，顺手清理掉节省内存
            if self.global_boxes[old_box_id]['volume'] <= 1e-8:
                del self.global_boxes[old_box_id]

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
        # 第一重 A: Absorption (1U 箱子微观订单流探测 - 究极进化版)
        # --------------------------------------------------
        elif self.status == "A1_WAIT_ABSORPTION":
            if price < self.target_zone['halo_low']:
                self._reset_to_idle()
                return None

            # 1. 宏观环境验证：必须有相对于 24h 全局均线的“绝对爆量”
            baseline_15s_vol = self.profile.get('avg_vol_1m', 100) / 4.0
            is_volume_spike = self.global_volume > (baseline_15s_vol * 2.5)

            # 2. 努力度验证：空头必须在疯狂砸盘 (CVD 为负且占比高)
            cvd_intensity = abs(self.global_cvd) / (self.global_volume + 1)
            is_heavy_selling = self.global_cvd < 0 and cvd_intensity > 0.3

            # 只有满足宏观爆量砸盘，才开启微观箱子扫描
            if is_volume_spike and is_heavy_selling and self.global_boxes:

                # 【性能 $O(1)$】：寻找 15 秒内的成交量控制点 (Micro POC Box)
                micro_poc_box = max(self.global_boxes.keys(), key=lambda k: self.global_boxes[k]['volume'])
                poc_volume = self.global_boxes[micro_poc_box]['volume']
                poc_delta = self.global_boxes[micro_poc_box]['delta']

                # --- 核心指标 1: 集中度 (即 Absorption Score 的变体) ---
                # 这 15 秒内，至少 50% 的成交量死死卡在同一个 1U 箱子里
                is_highly_concentrated = (poc_volume / self.global_volume) > 0.5

                # --- 核心指标 2: Delta 爆炸 (空头努力被全盘接收) ---
                # 该箱子内 Delta 极度偏负，证明是限价买单接住了所有市价砸盘
                is_seller_trapped = poc_delta < 0 and abs(poc_delta) > (poc_volume * 0.4)

                # --- 核心指标 3: 插针空气过滤 (Wick Rejection) ---
                # 统计 POC 箱子“下方”所有的成交量。如果下方全是“空气”，则判定为扫损插针
                volume_below_poc = sum(
                    box['volume'] for box_id, box in self.global_boxes.items() if box_id < micro_poc_box)
                is_wick_rejected = (volume_below_poc / self.global_volume) < 0.10

                # --- 核心指标 4: 价格企稳验证 (Price Recovery) ---
                # 价格必须已经站在 POC 箱子上沿附近，或者已经收回到箱子内
                is_price_recovered = price >= micro_poc_box

                # 只有当：集中度高 + Delta 负值爆炸 + 下方全是空气 + 价格已收回 -> 判定吸收成功
                if is_highly_concentrated and is_seller_trapped and is_wick_rejected and is_price_recovered:
                    self.status = "A2_WAIT_ACCUMULATION"

                    # 精准锁定防守底线：使用该爆量箱子的下沿
                    self.micro_tracker['absorption_price'] = float(micro_poc_box)
                    # 阻力位：该箱子的上沿，等待 A3 的放量突破
                    self.micro_tracker['micro_resistance'] = float(micro_poc_box + 1.0)
                    self.micro_tracker['a2_start_time'] = current_time

                    logger.info(f"🧱 [A1-吸收确认] 检出微观 POC 箱子: {micro_poc_box}")
                    logger.info(
                        f"📊 [指标] 集中度:{(poc_volume / self.global_volume):.1%}, 下方量占比:{(volume_below_poc / self.global_volume):.1%}")
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
