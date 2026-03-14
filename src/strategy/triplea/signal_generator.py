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
        self.radar_expansion = 0.5
        self.profile = {}

        # ==========================================
        # 🆕 专家级微观数据参数
        # ==========================================
        self.rolling_ticks = deque()
        self.rolling_window_sec = 15.0
        self.global_cvd = 0.0
        self.global_volume = 0.0
        self.global_boxes = {}

        # 🆕 动态自适应参数
        self.min_box_size = 0.25  # 保底最小箱子
        self.box_size_pct = 0.00015  # 价格的万分之1.5
        self.current_box_size = 0.25
        self.vol_spike_threshold = 2.0  # 爆量倍数
        self.delta_ratio_threshold = 0.35  # 空头攻击强度
        self.cluster_ratio_threshold = 0.45  # 成交密集度
        self.price_range_pct_limit = 0.0012  # 最大允许振幅 (0.12%)
        self.persistence_time = 3.0  # 吸收持续时间
        self.absorption_start_time = 0.0  # 吸收计时器

        # ==========================================
        # 📸 订单生命周期内存
        # ==========================================
        self.current_sl = 0.0
        self.current_tp = 0.0

        self.micro_tracker = {
            "absorption_price": 0.0,
            "micro_resistance": 0.0,
            "a2_start_time": 0.0
        }

    def update_macro_map(self, profile_data: Dict):
        self.tradable_zones = profile_data.get('tradable_zones', [])
        self.profile = profile_data

        reference_price = profile_data.get('POC', {}).get('center', 3000.0)
        new_box_size = max(self.min_box_size, reference_price * self.box_size_pct)

        # 🚀 极其优雅的防抖设计：只在 IDLE 且网格偏差大于 0.03U（对应以太坊约 200刀 的宏观位移）时才拉闸重启
        if self.status == "IDLE" and abs(new_box_size - self.current_box_size) > 0.03:
            logger.info(f"🔄 [IDLE安全期] 宏观网格换挡：{self.current_box_size:.4f} -> {new_box_size:.4f}。")

            self.current_box_size = new_box_size

            # 暴力清空，进入 15 秒盲区
            self.rolling_ticks.clear()
            self.global_boxes.clear()
            self.global_volume = 0.0
            self.global_cvd = 0.0

            logger.info("🧹 底层账本已清空，雷达重启中 (需等待 15 秒填满窗口)...")

    def process_tick(self, tick: Dict) -> Optional[Dict]:
        if self.status in ["LONG", "SHORT"]:
            return self._manage_position_by_tick(tick)

        price = tick['price']
        size = tick['size']
        current_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0

        # 1. 大管家极速更新底层 O(1) 数据流
        self._update_rolling_data(price, size, tick['side'], current_time)

        # 2. 优雅的状态机路由 (State Machine Routing)
        if self.status == "IDLE":
            return self._handle_idle(price)

        elif self.status == "A1_WAIT_ABSORPTION":
            return self._handle_absorption(price, current_time)

        elif self.status == "A2_WAIT_ACCUMULATION":
            return self._handle_accumulation(price, current_time)

        elif self.status == "A3_WAIT_AGGRESSION":
            return self._handle_aggression(price)

        return None

    def _handle_idle(self, price: float) -> Optional[Dict]:
        """阶段 0: 寻找交火区"""
        for zone in self.tradable_zones:
            if "MEGA" in zone['type']:
                continue
            if zone['halo_low'] <= price <= zone['halo_high']:
                self.status = "A1_WAIT_ABSORPTION"
                self.target_zone = zone
                return None
        return None

    def _handle_absorption(self, price: float, current_time: float) -> Optional[Dict]:
        if price < self.target_zone['halo_low']:
            self.absorption_start_time = 0.0
            self._reset_to_idle()
            return None

        if not self.global_boxes:
            return None

        baseline_15s_vol = self.profile.get('avg_vol_1m', 100) / 4.0
        if self.global_volume < (baseline_15s_vol * self.vol_spike_threshold):
            self.absorption_start_time = 0.0
            return None

        delta_ratio = abs(self.global_cvd) / (self.global_volume + 1e-8)
        if self.global_cvd >= 0 or delta_ratio < self.delta_ratio_threshold:
            self.absorption_start_time = 0.0
            return None

        center_box = max(self.global_boxes.keys(), key=lambda k: self.global_boxes[k]['volume'])

        # 使用锚定的网格大小计算左右偏移 (完美严丝合缝)
        left_box_1 = round((center_box - self.current_box_size) / self.current_box_size) * self.current_box_size
        left_box_2 = round((center_box - 2 * self.current_box_size) / self.current_box_size) * self.current_box_size
        right_box_1 = round((center_box + self.current_box_size) / self.current_box_size) * self.current_box_size
        right_box_2 = round((center_box + 2 * self.current_box_size) / self.current_box_size) * self.current_box_size

        cluster_vol = (
                self.global_boxes.get(left_box_2, {}).get('volume', 0.0) +
                self.global_boxes.get(left_box_1, {}).get('volume', 0.0) +
                self.global_boxes.get(center_box, {}).get('volume', 0.0) +
                self.global_boxes.get(right_box_1, {}).get('volume', 0.0) +
                self.global_boxes.get(right_box_2, {}).get('volume', 0.0)
        )

        cluster_ratio = cluster_vol / self.global_volume

        if cluster_ratio < self.cluster_ratio_threshold:
            self.absorption_start_time = 0.0
            return None

        min_price = min(self.global_boxes.keys())
        max_price = max(self.global_boxes.keys())
        mid_price = (max_price + min_price) / 2.0
        price_range_pct = (max_price - min_price) / (mid_price + 1e-8)

        if price_range_pct > self.price_range_pct_limit:
            self.absorption_start_time = 0.0
            return None

        # 🆕 [修复计时器吞 Tick] 只赋值，不立刻 return None，让它继续往下走
        if self.absorption_start_time == 0.0:
            self.absorption_start_time = current_time

        # 立刻检查是否已经满足时间要求 (即便是第一次触发，如果 persistence_time 被设得很小甚至 0，也能瞬间判定)
        if current_time - self.absorption_start_time >= self.persistence_time:
            self.status = "A2_WAIT_ACCUMULATION"

            self.micro_tracker['absorption_price'] = float(center_box)
            self.micro_tracker['micro_resistance'] = float(center_box + current_box_size)
            self.micro_tracker['a2_start_time'] = current_time

            self.absorption_start_time = 0.0

            efficiency = abs(self.global_cvd) / (price_range_pct + 1e-6)
            logger.info(f"🧱 [A1-吸收确认] 熬过 {self.persistence_time}秒 爆量轰炸！核心箱: {center_box}")
            logger.info(f"📊 [指标] 簇占比: {cluster_ratio: .1%}, Delta率: {delta_ratio: .1%}, 效率: {efficiency: .2f}")

        return None

    def _handle_accumulation(self, price: float, current_time: float) -> Optional[Dict]:
        """第二重 A2: Accumulation (时间锁与天花板探测)"""

        # 1. 破底防线：不管 CVD 怎么走，只要跌穿了 A1 确立的钛合金墙，立刻认怂撤退！
        if price < self.micro_tracker['absorption_price']:
            logger.debug("💥 [A2-积累失败] 吸收底线被击穿，主力防线崩溃，撤退！")
            self.absorption_start_time = 0.0
            self._reset_to_idle()
            return None

        # 2. 动态天花板：在横盘震荡期间，用一个 max() 函数，自然而然地把区间的最高点勾勒出来
        self.micro_tracker['micro_resistance'] = max(self.micro_tracker['micro_resistance'], price)

        # 3. 时间锁：强行要求盘口在这里“冷静”至少 5 秒钟 (完全契合你说的正常量、小幅震荡)
        # 只要这 5 秒内没跌破底线，A2 就算圆满完成！
        if current_time - self.micro_tracker['a2_start_time'] >= 5.0:
            self.status = "A3_WAIT_AGGRESSION"

            logger.info(f"🔋 [A2-积累完成] 历时 5 秒筹码换手完毕。")
            logger.info(
                f"🎯 [战场标定] 底线防守: {self.micro_tracker['absorption_price']}, 突破天花板: {self.micro_tracker['micro_resistance']}")
            return None

        return None

    def _handle_aggression(self, price: float) -> Optional[Dict]:
        """第三重 A: Aggression (拔枪开火)"""
        if price < self.micro_tracker['absorption_price']:
            self._reset_to_idle()
            return None

        if price > self.micro_tracker['micro_resistance'] and self.global_cvd > (self.global_volume * 0.15):
            logger.info(f"⚔️ [A3-攻击达成] 多头放量突破积累区！全军出击做多！")

            # [修复] 加上 .get() 安全保底，万一取不到 POC，给一个默认的保底止盈（比如现价 + 10刀）
            sl = self.micro_tracker['absorption_price'] - 1.0
            poc_data = self.profile.get('POC', {})
            tp = poc_data.get('center', price + 10.0)

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

    def _update_rolling_data(self, price: float, size: float, side: str, current_time: float):
        tick_delta = size if side == 'buy' else -size

        # 【微调1】队列里还是老老实实存原始的 price，为了以后可能的“重铸”做准备
        self.rolling_ticks.append((current_time, tick_delta, size, price))

        self.global_cvd += tick_delta
        self.global_volume += size

        # 进场：用当前的绝对网格装箱
        box_id = round(price / self.current_box_size) * self.current_box_size
        if box_id not in self.global_boxes:
            self.global_boxes[box_id] = {'volume': 0.0, 'delta': 0.0}
        self.global_boxes[box_id]['volume'] += size
        self.global_boxes[box_id]['delta'] += tick_delta

        # 退场：用当前的绝对网格扣减
        while self.rolling_ticks and current_time - self.rolling_ticks[0][0] > self.rolling_window_sec:
            old_time, old_delta, old_size, old_price = self.rolling_ticks.popleft()

            self.global_cvd -= old_delta
            self.global_volume -= old_size

            old_box_id = round(old_price / self.current_box_size) * self.current_box_size
            self.global_boxes[old_box_id]['volume'] -= old_size
            self.global_boxes[old_box_id]['delta'] -= old_delta

            if self.global_boxes[old_box_id]['volume'] <= 1e-8:
                del self.global_boxes[old_box_id]

    def _reset_to_idle(self):
        self.status = "IDLE"
        self.target_zone = None
        self.absorption_start_time = 0.0  # 🆕 确保计时器绝对清零！
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
