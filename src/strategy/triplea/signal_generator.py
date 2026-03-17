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
        self.macro_zones = []  # 👈 🚀 新增：专门用于存储 24 小时宏观地图用于止盈
        self.target_zone = None
        self.profile = {}

        # ==========================================
        # 🆕 专家级微观数据参数
        # ==========================================
        self.rolling_ticks = deque()
        self.rolling_window_sec = 15.0
        self.global_cvd = 0.0
        self.global_volume = 0.0
        self.global_boxes = {}

        # 🚀 新增：A3 专属的 3 秒极致 O(1) 动量账本
        self.rolling_ticks_3s = deque()
        self.recent_cvd_3s = 0.0
        self.recent_vol_3s = 0.0

        # 🆕 动态自适应参数 - 基于轨迹矿工分析优化
        self.min_box_size = 0.25  # 保底最小箱子
        self.box_size_pct = 0.00015  # 价格的万分之1.5
        self.current_box_size = 0.25
        self.vol_spike_threshold = 1.8  # 相对爆量倍数（原2.0，降低以提高A1检测率）
        self.min_absorption_usdt = 3_000_000.0  # 🚨 绝对爆量门槛：300万 USDT（原500万，降低以适配更多场景）
        self.delta_ratio_threshold = 0.30  # 空头攻击强度（原0.35，降低以提高检测率）
        self.cluster_ratio_threshold = 0.45  # 成交密集度
        self.price_range_pct_limit = 0.0012  # 最大允许振幅 (0.12%)
        self.persistence_time = 3.0  # 吸收持续时间
        self.absorption_start_time = 0.0  # 吸收计时器

        # A3攻击阶段参数（基于轨迹矿工分析优化）
        self.a3_req_vol_multiplier = 2.0  # 成交量突增倍数（原动态2.0-4.0，固定为2.0）
        self.a3_req_delta_ratio = 0.35  # 净买卖比阈值（原动态0.30-0.50，固定为0.35）

        # 新增过滤条件（基于轨迹矿工分析）
        self.min_max_volume = 120_000  # 最小最大成交量要求（原150k，调整为120k）
        self.volume_peak_position_threshold = 0.35  # 成交量峰值位置阈值（原0.4，调整为0.35）

        # ==========================================
        # 📸 订单生命周期内存
        # ==========================================
        self.current_sl = 0.0
        self.current_tp = 0.0

        # 🚀 新增：120秒大局观轨迹记忆 (解决插针和顺势问题)
        self.context_prices = deque()
        self.context_window_sec = 120.0

        self.micro_tracker = {
            "absorption_price": 0.0,
            "micro_resistance": 0.0,
            "micro_support": 0.0,  # 新增：空头支撑位
            "direction": None,  # "LONG" 或 "SHORT"，表示交易方向
            "a2_start_time": 0.0,
            "allowed_direction": None,  # 👈 新增：允许的开仓方向钢印
            "history_start_time": 0.0,  # 👈 新增：历史数据开始时间
            "volume_history": [],  # 👈 新增：历史成交量记录
            "cvd_history": []  # 👈 新增：历史CVD记录
        }

    def _process_zones(self, raw_zones):
        """内部辅助：安全拷贝阵地列表"""
        safe_tradable_zones = []
        for zone in raw_zones:
            safe_zone = zone.copy()
            safe_tradable_zones.append(safe_zone)
        return safe_tradable_zones

    def update_maps(self, short_profile: Dict, long_profile: Dict):
        """🚀 双轨雷达接收：短线管进场，长线管止盈"""
        self.profile = short_profile

        # 1. 战术地图 (8小时)：日常打仗、A1吸收全靠它
        self.tradable_zones = self._process_zones(short_profile.get('tradable_zones', []))

        # 2. 战略地图 (24小时)：专门用来寻找极高盈亏比的止盈点
        self.macro_zones = self._process_zones(long_profile.get('tradable_zones', []))

        # 3. 网格自适应 (必须锚定【短线地图】的 POC)
        reference_price = short_profile.get('POC', {}).get('center', 3000.0)
        new_box_size = max(self.min_box_size, reference_price * self.box_size_pct)

        # 🚀 极其优雅的防抖设计：只在 IDLE 且网格偏差大于 0.03U 时才拉闸重启
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
        in_zone = False

        for zone in self.tradable_zones:
            if "MEGA" in zone['type'] or zone['type'] == "POC":
                continue

            if zone['zone_low'] <= price <= zone['zone_high']:
                in_zone = True
                self.status = "A1_WAIT_ABSORPTION"
                self.target_zone = zone

                # 获取当前框的唯一身份指纹
                current_zone_key = (zone['zone_low'], zone['zone_high'])
                locked_key = self.micro_tracker.get('locked_zone_key')

                # 🚀 钢印防抖机制：如果还在之前的阵地里摩擦，绝不重新计算方向！直接沿用！
                if locked_key == current_zone_key and self.micro_tracker.get('allowed_direction', 'ANY') != 'ANY':
                    pass  # 什么都不用做，坚守上一轮留下的 allowed_direction
                else:
                    # 第一次进这个阵地，用 120 秒回溯查出身，并打上钢印！
                    allowed_dir = self._get_approach_direction(zone['zone_low'], zone['zone_high'])
                    self.micro_tracker['locked_zone_key'] = current_zone_key
                    self.micro_tracker['allowed_direction'] = allowed_dir

                return None

        # 🚀 真空地带宽容度检查：防插针把钢印洗掉
        if not in_zone and self.micro_tracker.get('locked_zone_key') is not None:
            locked_key = self.micro_tracker['locked_zone_key']
            zone_low, zone_high = locked_key

            # 如果价格已经彻底跑偏（比如偏离阵地超过 2 个箱子）
            # 这才视为真突破/真跌破，彻底抹除阵地钢印，准备迎接下一场战役！
            if price < zone_low - (self.current_box_size * 2.0) or price > zone_high + (self.current_box_size * 2.0):
                self.micro_tracker['locked_zone_key'] = None
                self.micro_tracker['allowed_direction'] = "ANY"

        return None

    def _handle_absorption(self, price: float, current_time: float) -> Optional[Dict]:
        zone_low = self.target_zone['zone_low']
        zone_high = self.target_zone['zone_high']

        if price < zone_low or price > zone_high:
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

        # 🚀 基于轨迹矿工分析新增：最小最大成交量要求 (WIN平均221k vs LOSS平均112k)
        if self.global_volume < self.min_max_volume:
            self._log_debug(f"🚫 [成交量过滤] 15秒成交量 {self.global_volume:.0f} < 最小要求 {self.min_max_volume}，拒接！")
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

        # 🚀 V2.3 核心杀招：顺势与扫损物理过滤器
        allowed_dir = self.micro_tracker.get('allowed_direction', 'ANY')
        if allowed_dir != 'ANY' and direction != allowed_dir:
            self._log_debug(f"🚫 [上下文过滤] 轨迹要求只能 {allowed_dir}，但当前底部呈现 {direction} 异动，视为逆势，拒接！")
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
            # 👇 初始化历史数据记录（用于CVD方向一致性和成交量峰值位置检查）
            self.micro_tracker['history_start_time'] = current_time
            self.micro_tracker['cumulative_volume'] = 0.0  # 从A1开始的累计成交量
            self.micro_tracker['cumulative_cvd'] = 0.0     # 从A1开始的累计CVD变化
            self.micro_tracker['volume_history'] = []      # 时间-成交量历史（用于峰值位置检查）
            self.micro_tracker['cvd_history'] = []         # 时间-CVD历史

            # 根据交易方向设置关键价位
            self.micro_tracker['micro_resistance'] = float(center_box + self.current_box_size)
            self.micro_tracker['micro_support'] = float(center_box - self.current_box_size)
            direction_cn = "多头" if direction == "LONG" else "空头"
            self._log_info(
                f"🧱 [A1-{direction_cn}吸收确认] 熬过 {self.persistence_time}秒 爆量轰炸！核心箱: {center_box}")

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
        """第三重 A3: Aggression (3秒微观动量确认拔枪)"""
        price = tick['price']
        current_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0
        direction = self.micro_tracker.get('direction')

        if direction is None:
            self._reset_to_idle()
            return None

        # 🚀 终极风控 1：Time-To-Live (TTL) 时间过期过滤
        setup_age = current_time - self.absorption_start_time
        if setup_age > 1800:
            self._log_debug(f"⏳ [A3-状态过期] 潜伏时间过长 ({setup_age:.0f}秒)，吸收势能已彻底消散，重置雷达！")
            self._reset_to_idle()
            return None

        # 🚀 极致 O(1) 提取 3 秒向量账本，0 循环！
        lookback_sec = 3.0
        recent_vol = self.recent_vol_3s
        recent_cvd = self.recent_cvd_3s
        # 队列最左边的元素，就是 3 秒前那一瞬间的“起跑点价格”
        start_price = self.rolling_ticks_3s[0][3] if self.rolling_ticks_3s else price

        baseline_3s_vol = (self.profile.get('avg_vol_1m', 60.0) / 60.0) * lookback_sec
        ceiling = self.micro_tracker['micro_resistance']
        floor = self.micro_tracker['micro_support']
        poc_price = self.profile.get('POC', {}).get('center', price)

        # 根据交易方向进行不同的价格检查
        if direction == "LONG":
            # 1. 跌破吸收底线：主力防守失败
            if price < self.micro_tracker['absorption_price']:
                self._log_debug("💥 [A3-多头攻击失败] 吸收底线被击穿，多头攻击取消！")
                self._reset_to_idle()
                return None

            breakout_threshold = ceiling + (self.current_box_size * 0.5)
            fomo_threshold = breakout_threshold + (self.current_box_size * 4.0)

            # 2. 无量慢涨越过极限警戒线，视为错过最佳突破口，放弃
            if price > fomo_threshold:
                self._log_debug("💥 [A3-错过点火] 价格无量慢涨，已飘过最佳突破口，拒接！")
                self._reset_to_idle()
                return None

            # 3. 在点火区内考核向量与纯度
            if breakout_threshold < price <= fomo_threshold:
                # --- 向量轨迹检查 (Vector Trajectory) ---
                max_start_price = ceiling + (self.current_box_size * 1.5)
                if start_price > max_start_price:
                    self._log_debug(f"💥 [A3-追高拦截] 3秒前起跑点({start_price})已远离天花板({ceiling})，拒接！")
                    self._reset_to_idle()
                    return None

                if price <= start_price or price < ceiling:
                    return None  # 轨迹不是有效向上的突破

                # --- 动态纯度检查 (Dynamic Momentum Filter) - 基于轨迹矿工分析优化 ---
                boxes_above = (price - breakout_threshold) / self.current_box_size
                # 使用实例变量中的A3参数
                is_volume_spike = recent_vol > (baseline_3s_vol * self.a3_req_vol_multiplier)
                delta_ratio_recent = recent_cvd / (recent_vol + 1e-8)
                is_strong_momentum = delta_ratio_recent > self.a3_req_delta_ratio

                # 🚀 基于轨迹矿工分析新增：CVD方向一致性检查
                if direction == "LONG" and self.micro_tracker.get('cumulative_cvd', 0) >= 0:
                    self._log_debug(f"🚫 [CVD方向过滤] LONG交易但累计CVD非负({self.micro_tracker.get('cumulative_cvd', 0):.0f})，拒接！")
                    self._reset_to_idle()
                    return None

                # 🚀 基于轨迹矿工分析新增：成交量峰值位置检查
                volume_history = self.micro_tracker.get('volume_history', [])
                if len(volume_history) >= 2:
                    # 找到成交量峰值的位置
                    max_volume = max(v for _, v in volume_history)
                    # 找到第一个达到最大值的记录
                    max_time = volume_history[0][0]  # 默认值
                    for t, v in volume_history:
                        if v == max_volume:
                            max_time = t
                            break
                    total_duration = current_time - self.micro_tracker.get('history_start_time', current_time)
                    if total_duration > 0:
                        peak_position = (max_time - self.micro_tracker.get('history_start_time', current_time)) / total_duration
                        if peak_position < self.volume_peak_position_threshold:
                            self._log_debug(f"🚫 [峰值位置过滤] 成交量峰值位置{peak_position:.2f} < 阈值{self.volume_peak_position_threshold}，拒接！")
                            self._reset_to_idle()
                            return None

                if is_volume_spike and is_strong_momentum:
                    self._log_info(
                        f"⚔️ [A3-多头攻击达成] {lookback_sec}秒内爆量 {recent_vol:.2f} (>{self.a3_req_vol_multiplier}x), 净买入占比 {delta_ratio_recent:.1%}！轨迹: {start_price} -> {price}")

                    # ---------------------------------------------------------
                    # 🎯 动态结构性寻址与净盈亏比兜底 (全地形自适应)
                    # ---------------------------------------------------------
                    estimated_fee = price * 0.0010  
                    sl = self.micro_tracker['absorption_price'] - self.current_box_size
                    risk_distance = price - sl

                    net_risk = risk_distance + estimated_fee
                    min_gross_reward = (net_risk * 2.5) + estimated_fee

                    tp_target = None

                    if price < poc_price:
                        for zone in self.macro_zones:  
                            if 'VAH' in zone['type'] and zone.get('zone_low') >= price + min_gross_reward:
                                tp_target = zone.get('zone_low')
                                break
                    else:
                        for zone in reversed(self.macro_zones):  
                            if zone['center'] > price and zone['type'] == 'HVN' and zone.get('zone_low') >= price + min_gross_reward:
                                tp_target = zone.get('zone_low')
                                break

                    if tp_target is None:
                        tp_target = price + min_gross_reward
                        self._log_debug(f"🗺️ 宏观地图未找到前方阵地，启用纯数学 1:2.5 净盈亏比止盈: {tp_target:.2f}")

                    actual_gross_reward = tp_target - price
                    if actual_gross_reward < min_gross_reward:
                        self._log_warning(f"🚫 [风控拦截] 前方阵地太近！需盈利 {min_gross_reward:.2f}U，实际仅 {actual_gross_reward:.2f}U，放弃做多！")
                        self._reset_to_idle()
                        return None

                    # 状态机维护
                    self.current_sl = sl
                    self.current_tp = tp_target
                    self.status = direction

                    signal_score = (abs(delta_ratio_recent) * 100) + (recent_vol / baseline_3s_vol)

                    return {
                        "action": "BUY",
                        "entry_price": price,
                        "stop_loss": round(sl, 4),
                        "take_profit": round(tp_target, 4),
                        "signal_score": round(signal_score, 2),
                        "reason": "TRIPLE_A_COMPLETE"
                    }

        else:  # SHORT
            # 1. 突破吸收上线：主力防守失败
            if price > self.micro_tracker['absorption_price']:
                self._log_debug("💥 [A3-空头攻击失败] 吸收阻力位被突破，空头攻击取消！")
                self._reset_to_idle()
                return None

            breakdown_threshold = floor - (self.current_box_size * 0.5)
            fomo_threshold = breakdown_threshold - (self.current_box_size * 4.0)

            # 2. 无量慢跌越过极限警戒线，视为错过最佳突破口，放弃
            if price < fomo_threshold:
                self._log_debug("💥 [A3-错过点火] 价格无量慢跌，已飘过最佳跌破口，拒接！")
                self._reset_to_idle()
                return None

            # 3. 在点火区内考核向量与纯度
            if fomo_threshold <= price < breakdown_threshold:
                # --- 向量轨迹检查 (Vector Trajectory) ---
                min_start_price = floor - (self.current_box_size * 1.5)
                if start_price < min_start_price:
                    self._log_debug(f"💥 [A3-追空拦截] 3秒前起跑点({start_price})已远离地板({floor})，拒接！")
                    self._reset_to_idle()
                    return None

                if price >= start_price or price > floor:
                    return None  # 轨迹不是有效向下的跌破

                # --- 动态纯度检查 (Dynamic Momentum Filter) - 基于轨迹矿工分析优化 ---
                boxes_below = (breakdown_threshold - price) / self.current_box_size
                # 使用实例变量中的A3参数
                is_volume_spike = recent_vol > (baseline_3s_vol * self.a3_req_vol_multiplier)
                delta_ratio_recent = recent_cvd / (recent_vol + 1e-8)
                is_strong_momentum = delta_ratio_recent < -self.a3_req_delta_ratio

                # 🚀 基于轨迹矿工分析新增：CVD方向一致性检查
                if direction == "SHORT" and self.micro_tracker.get('cumulative_cvd', 0) <= 0:
                    self._log_debug(f"🚫 [CVD方向过滤] SHORT交易但累计CVD非正({self.micro_tracker.get('cumulative_cvd', 0):.0f})，拒接！")
                    self._reset_to_idle()
                    return None

                # 🚀 基于轨迹矿工分析新增：成交量峰值位置检查
                volume_history = self.micro_tracker.get('volume_history', [])
                if len(volume_history) >= 2:
                    # 找到成交量峰值的位置
                    max_volume = max(v for _, v in volume_history)
                    # 找到第一个达到最大值的记录
                    max_time = volume_history[0][0]  # 默认值
                    for t, v in volume_history:
                        if v == max_volume:
                            max_time = t
                            break
                    total_duration = current_time - self.micro_tracker.get('history_start_time', current_time)
                    if total_duration > 0:
                        peak_position = (max_time - self.micro_tracker.get('history_start_time', current_time)) / total_duration
                        if peak_position < self.volume_peak_position_threshold:
                            self._log_debug(f"🚫 [峰值位置过滤] 成交量峰值位置{peak_position:.2f} < 阈值{self.volume_peak_position_threshold}，拒接！")
                            self._reset_to_idle()
                            return None

                if is_volume_spike and is_strong_momentum:
                    self._log_info(
                        f"⚔️ [A3-空头攻击达成] {lookback_sec}秒内爆量 {recent_vol:.2f} (>{self.a3_req_vol_multiplier}x), 净卖出占比 {-delta_ratio_recent:.1%}！轨迹: {start_price} -> {price}")

                    # ---------------------------------------------------------
                    # 🎯 动态结构性寻址与净盈亏比兜底 (全地形自适应)
                    # ---------------------------------------------------------
                    estimated_fee = price * 0.0010  
                    sl = self.micro_tracker['absorption_price'] + self.current_box_size
                    risk_distance = sl - price

                    net_risk = risk_distance + estimated_fee
                    min_gross_reward = (net_risk * 2.5) + estimated_fee

                    tp_target = None

                    if price > poc_price:
                        for zone in self.macro_zones:  
                            if 'VAL' in zone['type'] and zone.get('zone_high') <= price - min_gross_reward:
                                tp_target = zone.get('zone_high')
                                break
                    else:
                        for zone in self.macro_zones:  
                            if zone['center'] < price and zone['type'] == 'HVN' and zone.get('zone_high') <= price - min_gross_reward:
                                tp_target = zone.get('zone_high')
                                break

                    if tp_target is None:
                        tp_target = price - min_gross_reward
                        self._log_debug(f"🗺️ 宏观地图未找到下方阵地，启用纯数学 1:2.5 净盈亏比止盈: {tp_target:.2f}")

                    actual_gross_reward = price - tp_target
                    if actual_gross_reward < min_gross_reward:
                        self._log_warning(f"🚫 [风控拦截] 前方阵地太近！需盈利 {min_gross_reward:.2f}U，实际仅 {actual_gross_reward:.2f}U，放弃做空！")
                        self._reset_to_idle()
                        return None

                    # 状态机维护
                    self.current_sl = sl
                    self.current_tp = tp_target
                    self.status = direction

                    signal_score = (abs(delta_ratio_recent) * 100) + (recent_vol / baseline_3s_vol)

                    return {
                        "action": "SELL",
                        "entry_price": price,
                        "stop_loss": round(sl, 4),
                        "take_profit": round(tp_target, 4),
                        "signal_score": round(signal_score, 2),
                        "reason": "TRIPLE_A_COMPLETE"
                    }

        return None

    def _update_rolling_data(self, price: float, size: float, side: str, current_time: float):
        tick_delta = size if side == 'buy' else -size

        # 记录A2/A3阶段的历史数据用于过滤检查
        if self.status in ["A2_WAIT_ACCUMULATION", "A3_WAIT_AGGRESSION"]:
            if hasattr(self, 'micro_tracker') and 'cumulative_volume' in self.micro_tracker:
                # 更新从A1开始的累计成交量和CVD
                self.micro_tracker['cumulative_volume'] += size
                self.micro_tracker['cumulative_cvd'] += tick_delta
                # 记录时间-成交量历史（用于峰值位置检查）
                # 每0.5秒记录一次，避免数据量过大
                if (not self.micro_tracker['volume_history'] or
                    current_time - self.micro_tracker['volume_history'][-1][0] >= 0.5):
                    self.micro_tracker['volume_history'].append((current_time, self.micro_tracker['cumulative_volume']))
                    self.micro_tracker['cvd_history'].append((current_time, self.micro_tracker['cumulative_cvd']))

        # 【微调1】队列里还是老老实实存原始的 price，为了以后可能的“重铸”做准备
        self.rolling_ticks.append((current_time, tick_delta, size, price))

        # 🚀 新增：更新大局观轨迹 (只存时间和价格)
        self.context_prices.append((current_time, price))
        while self.context_prices and current_time - self.context_prices[0][0] > self.context_window_sec:
            self.context_prices.popleft()

        self.global_cvd += tick_delta
        self.global_volume += size
        
        # 🚀 新增：极致 O(1) 维护 3 秒滑窗账本
        self.rolling_ticks_3s.append((current_time, tick_delta, size, price))
        self.recent_cvd_3s += tick_delta
        self.recent_vol_3s += size

        while self.rolling_ticks_3s and current_time - self.rolling_ticks_3s[0][0] > 3.0:
            _, old_delta, old_size, _ = self.rolling_ticks_3s.popleft()
            self.recent_cvd_3s -= old_delta
            self.recent_vol_3s -= old_size

        # 进场：用当前的绝对网格装箱
        box_id = round(price / self.current_box_size) * self.current_box_size
        if box_id not in self.global_boxes:
            self.global_boxes[box_id] = {'volume': 0.0, 'delta': 0.0}
        self.global_boxes[box_id]['volume'] += size
        self.global_boxes[box_id]['delta'] += tick_delta

        # 退场：用当前的绝对网格扣减 (15秒)
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
        # 🚀 专家级修复：打断状态机死循环，同时【保留阵地钢印】！防失忆！
        preserved_dir = self.micro_tracker.get('allowed_direction', 'ANY') if hasattr(self, 'micro_tracker') else 'ANY'
        preserved_key = self.micro_tracker.get('locked_zone_key', None) if hasattr(self, 'micro_tracker') else None

        self.status = "IDLE"
        self.target_zone = None
        self.absorption_start_time = 0.0
        self.micro_tracker = {
            "absorption_price": 0.0,
            "micro_resistance": 0.0,
            "micro_support": 0.0,
            "direction": None,
            "a2_start_time": 0.0,
            "allowed_direction": preserved_dir,  # 👈 继承方向
            "locked_zone_key": preserved_key,    # 👈 继承坐标
            "history_start_time": 0.0,           # 👈 历史数据开始时间
            "cumulative_volume": 0.0,            # 👈 从A1开始的累计成交量
            "cumulative_cvd": 0.0,               # 👈 从A1开始的累计CVD变化
            "volume_history": [],                # 👈 时间-成交量历史
            "cvd_history": []                    # 👈 时间-CVD历史
        }
        
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

    def _get_approach_direction(self, zone_low: float, zone_high: float) -> str:
        """
        🚀 时空回溯：倒查过去 2 分钟的轨迹，判断是从哪边撞进来的
        完美解决插针：如果是先在上方，再砸穿下方，再收回，由于我们从旧到新查，依然会判定为从上方来 (LONG)！
        """
        for ts, p in self.context_prices:
            if p > zone_high:
                return "LONG"  # 最早是从上方来的 -> 寻找支撑/底背离 -> 只能做多
            elif p < zone_low:
                return "SHORT"  # 最早是从下方来的 -> 寻找阻力/顶背离 -> 只能做空
        return "ANY"  # 2分钟内一直都在框里震荡

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
