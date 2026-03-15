from collections import deque
from typing import Dict, Optional

from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleASignalGenerator:
    def __init__(self, symbol: str = "ETH-USDT-SWAP", is_shadow: bool = False):
        self.symbol = symbol
        self.is_shadow = is_shadow
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
        self.vol_spike_threshold = 2.0  # 相对爆量倍数
        self.min_absorption_usdt = 10_000_000.0  # 🚨 绝对爆量门槛：1000万 USDT
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
            "micro_support": 0.0,  # 新增：空头支撑位
            "direction": None,  # "LONG" 或 "SHORT"，表示交易方向
            "a2_start_time": 0.0
        }

    def update_macro_map(self, profile_data: Dict):
        raw_zones = profile_data.get('tradable_zones', [])
        self.profile = profile_data

        # 建立一个全新的安全列表！
        safe_tradable_zones = []
        for zone in raw_zones:
            safe_zone = zone.copy()
            if "MEGA" not in safe_zone['type']:
                zone_width = safe_zone['zone_high'] - safe_zone['zone_low']
                safe_zone['halo_high'] = safe_zone['zone_high'] + (zone_width * self.radar_expansion)
                safe_zone['halo_low'] = safe_zone['zone_low'] - (zone_width * self.radar_expansion)

            # 把加工好的安全字典塞进新列表
            safe_tradable_zones.append(safe_zone)

        self.tradable_zones = safe_tradable_zones

        reference_price = profile_data.get('POC', {}).get('center', 3000.0)
        new_box_size = max(self.min_box_size, reference_price * self.box_size_pct)

        # 🚀 极其优雅的防抖设计：只在 IDLE 且网格偏差大于 0.03U（对应以太坊约 200刀 的宏观位移）时才拉闸重启
        if self.status == "IDLE" and abs(new_box_size - self.current_box_size) > 0.03:
            self._log_debug(f"🔄 [IDLE安全期] 宏观网格换挡：{self.current_box_size: .4f} -> {new_box_size: .4f}。")

            self.current_box_size = new_box_size

            # 暴力清空，进入 15 秒盲区
            self.rolling_ticks.clear()
            self.global_boxes.clear()
            self.global_volume = 0.0
            self.global_cvd = 0.0

            self._log_debug("🧹 底层账本已清空，雷达重启中 (需等待 15 秒填满窗口)...")

    def process_tick(self, tick: Dict) -> Optional[Dict]:
        price = tick['price']
        size = tick['size']
        current_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0

        # 1. 大管家极速更新底层 O(1) 数据流
        self._update_rolling_data(price, size, tick['side'], current_time)

        # 2. 优雅的状态机路由 (State Machine Routing)
        if self.status in ["LONG", "SHORT"]:
            return self._manage_position_by_tick(tick)
        elif self.status == "IDLE":
            return self._handle_idle(price)

        elif self.status == "A1_WAIT_ABSORPTION":
            return self._handle_absorption(price, current_time)

        elif self.status == "A2_WAIT_ACCUMULATION":
            return self._handle_accumulation(price, current_time)

        elif self.status == "A3_WAIT_AGGRESSION":
            return self._handle_aggression(tick)

        return None

    def _handle_idle(self, price: float) -> Optional[Dict]:
        """阶段 0: 寻找交火区"""
        for zone in self.tradable_zones:
            # 🚀 铁律：坚决不在绞肉机（纯 POC 或 MEGA 融合区）里开仓！
            if "MEGA" in zone['type'] or zone['type'] == "POC":
                continue

            if zone['halo_low'] <= price <= zone['halo_high']:
                self.status = "A1_WAIT_ABSORPTION"
                self.target_zone = zone
                return None
        return None

    def _handle_absorption(self, price: float, current_time: float) -> Optional[Dict]:
        halo_low = self.target_zone['halo_low']
        halo_high = self.target_zone['halo_high']

        if price < halo_low or price > halo_high:
            self.absorption_start_time = 0.0
            self._reset_to_idle()
            return None

        if not self.global_boxes:
            return None

        # ==========================================
        # 🛡️ 防伪滤镜 1：绝对 USDT 成交量底线 + 相对倍数
        # ==========================================
        # 直接乘：15秒内总张数 * 0.1(合约面值) * 现价 = 真实 USDT 金额
        current_vol_usdt = self.global_volume * 0.1 * price
        baseline_15s_vol = self.profile.get('avg_vol_1m', 100) / 4.0

        # 必须同时满足：1. 大于平时的相对爆量倍数；2. 绝对金额大于 1000 万 USDT
        if self.global_volume < (
                baseline_15s_vol * self.vol_spike_threshold) or current_vol_usdt < self.min_absorption_usdt:
            self.absorption_start_time = 0.0
            return None

        # ==========================================
        # 🛡️ 防伪滤镜 2：极其苛刻的净买卖比
        # ==========================================
        delta_ratio = abs(self.global_cvd) / (self.global_volume + 1e-8)
        if delta_ratio < self.delta_ratio_threshold:
            self.absorption_start_time = 0.0
            return None

        direction = "LONG" if self.global_cvd < 0 else "SHORT" if self.global_cvd > 0 else None
        if direction is None:
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
            self.micro_tracker['direction'] = direction
            self.micro_tracker['a2_start_time'] = current_time

            # 根据交易方向设置关键价位
            self.micro_tracker['micro_resistance'] = float(center_box + self.current_box_size)
            self.micro_tracker['micro_support'] = float(center_box - self.current_box_size)
            direction_cn = "多头" if direction == "LONG" else "空头"
            self._log_info(
                f"🧱 [A1-{direction_cn}吸收确认] 熬过 {self.persistence_time}秒 爆量轰炸！核心箱: {center_box}")

            self.absorption_start_time = 0.0

            efficiency = abs(self.global_cvd) / (price_range_pct + 1e-6)
            self._log_info(
                f"📊 [指标] 簇占比: {cluster_ratio: .1%}, Delta率: {delta_ratio: .1%}, 效率: {efficiency: .2f}")

        return None

    def _handle_accumulation(self, price: float, current_time: float) -> Optional[Dict]:
        """第二重 A2: Accumulation (静默换手)"""
        direction = self.micro_tracker.get('direction')

        # 1. 根据交易方向检查关键价位
        if direction == "LONG":
            # 多头积累：价格不能跌破吸收底线
            if price < self.micro_tracker['absorption_price']:
                self._log_debug("💥 [A2-多头积累失败] 吸收底线被击穿，主力防线崩溃，撤退！")
                self.absorption_start_time = 0.0
                self._reset_to_idle()
                return None
        elif direction == "SHORT":
            # 严格对称：只要涨破吸收核心价（天花板），空头防线即告崩溃！
            if price > self.micro_tracker['absorption_price']:
                self._log_debug("💥 [A2/A3-空头失败] 吸收天花板被突破，防线崩溃，撤退！")
                self._reset_to_idle()
                return None
        else:
            # 未知吸收类型，重置
            self._reset_to_idle()
            return None

        # 🚨 【专家级修复】删除了 max(micro_resistance, price)！
        # 绝不让洗盘期间的“毛刺假针”抬高我们的天花板！天花板由 A1 的网格结构直接锁定！

        # 2. 时间锁：强行熬过 5 秒换手期
        if current_time - self.micro_tracker['a2_start_time'] >= 5.0:
            self.status = "A3_WAIT_AGGRESSION"
            direction = self.micro_tracker.get('direction', 'UNKNOWN')
            self._log_info(f"🔋 [A2-{direction}积累完成] 历时 5 秒筹码换手完毕，等待突破。")
            return None

        return None

    def _handle_aggression(self, tick: Dict) -> Optional[Dict]:
        """第三重 A3: Aggression (1.5秒微观动量确认拔枪)"""
        price = tick['price']
        current_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0
        direction = self.micro_tracker.get('direction')

        if direction is None:
            self._reset_to_idle()
            return None

        # 根据交易方向进行不同的价格检查
        if direction == "LONG":
            # 多头攻击：价格不能跌破吸收底线
            if price < self.micro_tracker['absorption_price']:
                self._log_debug("💥 [A3-多头攻击失败] 吸收底线被击穿，多头攻击取消！")
                self._reset_to_idle()
                return None

            # 🚨 【专家级修复】增加突破的 Buffer (缓冲区)，必须实质性越过天花板 (比如高出半个箱子)，防假突破
            breakout_threshold = self.micro_tracker['micro_resistance'] + (self.current_box_size * 0.5)
            should_check_breakout = price > breakout_threshold

        else:  # SHORT
            # 空头攻击：价格不能突破吸收阻力位
            if price > self.micro_tracker['absorption_price']:
                self._log_debug("💥 [A3-空头攻击失败] 吸收阻力位被突破，空头攻击取消！")
                self._reset_to_idle()
                return None

            # 空头攻击：检查价格跌破支撑位（支撑位减去半个箱子作为缓冲区）
            breakdown_threshold = self.micro_tracker['micro_support'] - (self.current_box_size * 0.5)
            should_check_breakout = price < breakdown_threshold

        if should_check_breakout:
            # 🚀 价格破位！立刻启动“1.5秒微观动量”扫描！
            recent_vol = 0.0
            recent_cvd = 0.0
            lookback_sec = 1.5

            # 从队列最末尾（最新 Tick）往前遍历，只取最近 1.5 秒的数据
            for t in reversed(self.rolling_ticks):
                if current_time - t[0] <= lookback_sec:
                    recent_cvd += t[1]  # tick_delta
                    recent_vol += t[2]  # size
                else:
                    break  # 超过 1.5 秒，直接打断，极其节省算力！

            # 1. 局部 Volume Spike 判定 (这 1.5 秒的量，必须大于平时 1.5 秒均量的 2 倍)
            baseline_1_5s_vol = (self.profile.get('avg_vol_1m', 60.0) / 60.0) * lookback_sec
            is_volume_spike = recent_vol > (baseline_1_5s_vol * 2.0)

            # 2. 局部 Delta Ratio 判定
            delta_ratio_recent = recent_cvd / (recent_vol + 1e-8)

            if direction == "LONG":
                # 多头攻击：需要主动买盘占优（净买入）
                is_strong_momentum = delta_ratio_recent > 0.30
                log_prefix = "多头"
                momentum_desc = f"净买入占比 {delta_ratio_recent:.1%}"
            else:  # SHORT
                # 空头攻击：需要主动卖盘占优（净卖出）
                is_strong_momentum = delta_ratio_recent < -0.30
                log_prefix = "空头"
                momentum_desc = f"净卖出占比 {-delta_ratio_recent:.1%}"

            if is_volume_spike and is_strong_momentum:
                self._log_info(
                    f"⚔️ [A3-{log_prefix}攻击达成] 1.5秒内爆量 {recent_vol:.2f}, {momentum_desc}，真突破确立！")

                # ---------------------------------------------------------
                # 🎯 动态结构性寻址与净盈亏比兜底 (全地形自适应)
                # ---------------------------------------------------------
                estimated_fee = price * 0.0010  # 估算双边千分之一手续费

                if direction == "LONG":
                    sl = self.micro_tracker['absorption_price'] - self.current_box_size
                    risk_distance = price - sl

                    # 提前算好 2.5 倍净盈亏比所需的最小盘面利润
                    net_risk = risk_distance + estimated_fee
                    min_gross_reward = (net_risk * 2.5) + estimated_fee

                    tp_target = None
                    poc_price = self.profile.get('POC', {}).get('center', float('inf'))

                    # 🔍 1. 结构性寻址 (多头)
                    if price < poc_price:
                        # 抄底模式：寻找上方宏观天花板 (VAH) 的下沿
                        for zone in self.tradable_zones:
                            # 必须确保阵地下沿的距离，大于最低盈亏比底线
                            if 'VAH' in zone['type'] and zone.get('halo_low') >= price + min_gross_reward:
                                tp_target = zone.get('halo_low')
                                break
                    else:
                        # 顺势模式：向上寻找最近的 HVN 的下沿
                        for zone in reversed(self.tradable_zones):
                            if zone['center'] > price and zone['type'] == 'HVN' and zone.get(
                                    'halo_low') >= price + min_gross_reward:
                                tp_target = zone.get('halo_low')
                                break

                    # 🛡️ 2. 降级兜底 (如果地图上找不到结构，直接强行按 2.5R:R 算止盈)
                    if tp_target is None:
                        tp_target = price + min_gross_reward
                        self._log_debug(f"🗺️ 宏观地图未找到前方阵地，启用纯数学 1:2.5 净盈亏比止盈: {tp_target:.2f}")

                    # ⚖️ 3. 终极风控拦截 (如果找到了结构，但结构离得太近，不够塞牙缝，直接拒接开仓！)
                    actual_gross_reward = tp_target - price
                    if actual_gross_reward < min_gross_reward:
                        self._log_warning(
                            f"🚫 [风控拦截] 前方阵地太近！需盈利 {min_gross_reward:.2f}U，实际仅 {actual_gross_reward:.2f}U，放弃做多！")
                        self._reset_to_idle()
                        return None

                    tp = tp_target
                    action = "BUY"

                else:  # SHORT
                    sl = self.micro_tracker['absorption_price'] + self.current_box_size
                    risk_distance = sl - price

                    net_risk = risk_distance + estimated_fee
                    min_gross_reward = (net_risk * 2.5) + estimated_fee

                    tp_target = None
                    poc_price = self.profile.get('POC', {}).get('center', -float('inf'))

                    # 🔍 1. 结构性寻址 (空头)
                    if price > poc_price:
                        # 摸顶模式：寻找下方宏观地板 (VAL) 的上沿
                        for zone in self.tradable_zones:
                            # 必须确保阵地上沿的距离，大于最低盈亏比底线
                            if 'VAL' in zone['type'] and zone.get('halo_high') <= price - min_gross_reward:
                                tp_target = zone.get('halo_high')
                                break
                    else:
                        # 顺势模式：向下寻找最近的 HVN 的上沿
                        for zone in self.tradable_zones:
                            if zone['center'] < price and zone['type'] == 'HVN' and zone.get(
                                    'halo_high') <= price - min_gross_reward:
                                tp_target = zone.get('halo_high')
                                break

                    # 🛡️ 2. 降级兜底
                    if tp_target is None:
                        tp_target = price - min_gross_reward
                        self._log_debug(f"🗺️ 宏观地图未找到下方阵地，启用纯数学 1:2.5 净盈亏比止盈: {tp_target:.2f}")

                    # ⚖️ 3. 终极风控拦截
                    actual_gross_reward = price - tp_target
                    if actual_gross_reward < min_gross_reward:
                        self._log_warning(
                            f"🚫 [风控拦截] 前方阵地太近！需盈利 {min_gross_reward:.2f}U，实际仅 {actual_gross_reward:.2f}U，放弃做空！")
                        self._reset_to_idle()
                        return None

                    tp = tp_target
                    action = "SELL"

                self.current_sl = sl
                self.current_tp = tp
                self.status = direction

                # 【专家级修复】输出信号分数 (Score)，供未来的资金管理模块决定仓位大小
                signal_score = (abs(delta_ratio_recent) * 100) + (recent_vol / baseline_1_5s_vol)

                return {
                    "action": action,
                    "entry_price": price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "signal_score": round(signal_score, 2),
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
        self.absorption_start_time = 0.0
        self.micro_tracker = {
            "absorption_price": 0.0,
            "micro_resistance": 0.0,
            "micro_support": 0.0,
            "direction": None,
            "a2_start_time": 0.0
        }

        # 🚀 专家级修复：打断状态机死循环！
        # 如果订单/侦察失败被重置，说明之前的微观动量是不连贯的或有毒的。
        # 暴力清空 15 秒滑动窗口，强制引擎进入“冷却盲区”，等待下一波全新行情的蓄力！
        self.rolling_ticks.clear()
        self.global_boxes.clear()
        self.global_volume = 0.0
        self.global_cvd = 0.0
        self._log_debug("🧹 状态已重置，底层账本已排空，等待新的资金入场...")

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
            self._log_info(f"🏁 订单终结！触发原因: {signal['reason']}，成交价: {price}。")
            self._reset_to_idle()
            self.current_sl = 0.0
            self.current_tp = 0.0

        return signal

    # ==========================================
    # 🔇 日志消音器：如果是影子引擎，就闭嘴不打印日常刷屏
    # ==========================================
    def _log_info(self, msg: str):
        if not self.is_shadow:
            logger.info(msg)

    def _log_debug(self, msg: str):
        if not self.is_shadow:
            logger.debug(msg)

    def _log_warning(self, msg: str):
        if not self.is_shadow:
            logger.warning(msg)

    def _log_error(self, msg: str):
        if not self.is_shadow:
            logger.error(msg)
