import json
import logging
import os
import time
from collections import deque
from typing import Optional

from src.context.market_context import MarketContext
from src.strategy.orderflow.orderflow_config import OrderFlowConfig
from src.utils.log import get_logger

logger = get_logger(__name__)
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(message)s')

# 🌟 优雅解析：获取当前文件所在目录，向上推三层找到项目根目录
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
data_dir = os.path.join(project_root, "data")

# 如果 data 文件夹不存在，系统自动帮你建一个
if not os.path.exists(data_dir):
    os.makedirs(data_dir)


class OrderFlowMath:
    def __init__(self, config=None, context: Optional[MarketContext] = None):
        # 处理配置：支持字典或OrderFlowConfig对象
        if config is None:
            self.config = OrderFlowConfig()
        elif isinstance(config, dict):
            self.config = OrderFlowConfig.from_dict(config)
        elif isinstance(config, OrderFlowConfig):
            self.config = config
        else:
            raise TypeError(f"config参数类型错误，应为dict或OrderFlowConfig，实际为{type(config)}")

        self.context = context

        # 记录配置加载
        logger.info(f"[OrderFlowMath] 配置加载完成: contract_size={self.config.contract_size}, "
                    f"armed_threshold=${abs(self.config.armed_threshold_usdt) / 1_000_000:.1f}M, "
                    f"fire_cooldown={self.config.fire_cooldown_sec}s")

        # ==========================================
        # 🔧 运行时状态初始化
        # ==========================================
        self.cvd = 0.0
        self.current_price = 0.0

        # 历史锚点（大大减轻 CPU 压力）
        self.snapshots = deque(maxlen=self.config.snapshot_count)  # 只存过去 snapshot_window_minutes 分钟
        self.last_snapshot_time = time.time()

        # 🔫 极速状态机
        self.state = "IDLE"  # 状态：IDLE (空闲) -> ARMED (上膛)
        self.armed_time = 0.0
        self.local_low = 0.0
        self.local_low_cvd = 0.0

        # 防止连续开火的冷却锁
        self.last_fire_time = 0.0
        self.last_stop_loss_price = 0.0
        self.last_stop_loss_time = 0.0
        self.max_price_since_stop = 0.0

        # 波段状态
        self.broad_fired_this_round = False
        self.round_max_effort_m = 0.0
        self.round_max_resistance = 0.0

        # 🧠 动态流动性记忆 (带本地持久化存档)
        self.memory_file = os.path.join(data_dir, "ema_memory.json")
        self._load_ema_memory()

        # 区间最低价追踪
        self.interval_min_price = float('inf')

    def process_tick(self, tick: dict):
        """每秒可能接收几十上百个tick，全速 O(1) 运算"""
        self.current_price = tick['price']
        size = tick['size']
        side = tick['side']
        current_ts = tick['ts']

        # 🌟 逻辑 A：记录止损后的反弹最高点，用于判断“回马枪”还是“新行情”
        if self.last_stop_loss_price > 0:
            self.max_price_since_stop = max(self.max_price_since_stop, self.current_price)

        # 1. 极速更新全局 CVD
        if side == 'buy':
            self.cvd += size
        else:
            self.cvd -= size

        # 🌟 必须加上这行：每次 Tick 进来都更新区间最低价！
        self.interval_min_price = min(self.interval_min_price, self.current_price)

        # 2. 维护历史锚点 (仅为了获取"3分钟前"的CVD基准，极低频操作)
        if current_ts - self.last_snapshot_time >= self.config.snapshot_interval_seconds:
            self.snapshots.append({
                'ts': current_ts,
                'cvd': self.cvd,
                'price': self.current_price,  # 🌟 新增：记录当时的现价，用于计算真实跌幅
                'min_price': self.interval_min_price
            })
            self.last_snapshot_time = current_ts
            self.interval_min_price = self.current_price

        # 刚开机，数据不够 analysis_snapshot_count 个快照，保持沉默防飞刀
        if len(self.snapshots) < self.config.analysis_snapshot_count:
            return None

        # ==========================================
        # 🧠 毫秒级状态机逻辑开始
        # ==========================================
        # 获取 analysis_snapshot_count 个快照前的 CVD 作为基准
        snapshot_3m_ago = self.snapshots[-self.config.analysis_snapshot_count]
        recent_cvd_delta_usdt = (self.cvd - snapshot_3m_ago['cvd']) * self.config.contract_size * self.current_price

        # 阶段 1：触发上膛 (ARMED)
        # 只要 3 分钟内被砸了 500 万刀，系统立刻进入备战状态，死死盯住盘口
        if self.state == "IDLE":
            if recent_cvd_delta_usdt < -self.config.armed_threshold_usdt and (
                    current_ts - self.last_fire_time > self.config.fire_cooldown_sec):
                self.state = "ARMED"
                self.armed_time = current_ts
                self.local_low = self.current_price
                self.local_low_cvd = self.cvd
                # 每次新波段上膛时，才给予一次向科考船汇报 BROAD 的权利！
                self.broad_fired_this_round = False
                return None

        # 阶段 2：让子弹飞 (Tracking Bottom) & 击发 (FIRE)
        elif self.state == "ARMED":
            # ==========================================
            # 🛡️ 智能空间拦截 (只有满足以下所有条件才拦截)
            # ==========================================
            if self.last_stop_loss_price > 0:
                is_recent = (current_ts - self.last_stop_loss_time) < self.config.recent_stop_loss_window  # 15分钟内算近期
                is_not_dipped = self.current_price >= self.last_stop_loss_price  # 没跌破前止损位
                is_no_rebound = self.max_price_since_stop < self.last_stop_loss_price * self.config.rebound_threshold  # 没反弹超过0.5%

                if is_recent and is_not_dipped and is_no_rebound:
                    # 只有在 15分钟内、没跌破新低、且中间没像样反弹的情况下，才认为是“高频磨损”，拦截！
                    return None

            # 动作 A：价格还在创新低！说明没跌完，绝对不开火！不断下移防线！
            if self.current_price < self.local_low:
                self.local_low = self.current_price
                self.local_low_cvd = self.cvd
                self.armed_time = current_ts
                self.ema_updated_this_round = False  # 🌟 解锁！因为砸盘量变大了，等下需要重新记录

            # 🌟 提前计算出反弹幅度，供动作 B 和 动作 C 共同判定
            bounce_pct = (self.current_price - self.local_low) / self.local_low * 100

            # 动作 B：解除武装 (耐心耗尽 或 价格静默漂移)
            is_timeout = (current_ts - self.armed_time > self.config.patience_latency)  # 🌟 修复：允许在坑底耐心潜伏 1 个小时 (3600秒)！
            is_price_drifted = (bounce_pct > self.config.price_silence_threshold)  # 🌟 防护网：如果静悄悄地反弹超过 0.5%，说明底部已过，放弃接盘

            if is_timeout or is_price_drifted:
                self.state = "IDLE"
                self._commit_ema_memory()

            # 动作 C：绝地反击或极限吸收！
            else:
                micro_cvd_usdt = (self.cvd - self.local_low_cvd) * self.config.contract_size * self.current_price

                price_3m_ago = snapshot_3m_ago['price']
                effort_m = abs(recent_cvd_delta_usdt) / 1_000_000
                price_drop_pct = (price_3m_ago - self.local_low) / price_3m_ago * 100
                safe_drop = max(price_drop_pct, self.config.safe_drop_min)
                current_resistance = effort_m / (safe_drop * 100)

                # 🌟 持续记录本轮最大值 (完美锁定庄家在这个波段展现出的最大暴力)
                self.round_max_effort_m = max(self.round_max_effort_m, effort_m)
                self.round_max_resistance = max(self.round_max_resistance, current_resistance)

                # ==========================================
                # 🧊 轨 0 & 轨 1：用【巅峰纪录】计算异常度，拒绝记忆衰退！
                # ==========================================
                # 🌟 修复：用 round_max 替代瞬时的 effort_m
                effort_anomaly = self.round_max_effort_m / self.avg_wave_effort_m
                resistance_anomaly = self.round_max_resistance / self.avg_resistance_bps

                # 冰山吸收条件：
                # (只要这个波段曾经爆发出 > 1.5 倍的砸盘，并且曾经遭遇过 > 4.0 倍的阻力，且目前价格被死死按住)
                cond_absorption = (
                        effort_anomaly > self.config.dump_anomaly_threshold and
                        price_drop_pct < self.config.price_drop_threshold and
                        resistance_anomaly > self.config.resistance_anomaly_threshold
                )

                # ==========================================
                # 🔥 轨 1 & 轨 2：动态 V型反转
                # ==========================================
                # 计算反弹资金占比 (反击量 / 砸盘量)
                if effort_m > 0:
                    rebound_ratio = (micro_cvd_usdt / 1_000_000) / effort_m
                else:
                    rebound_ratio = 0.0

                # (哪怕反弹来得晚，只要这波曾经有过 > 1.2 倍的狂砸，现在反包了，照样开火！)
                # 🌟 进化版 V 反判定 (基于数据校准)：
                cond_v_reversal = (
                        -recent_cvd_delta_usdt > 2_000_000 and  # 砸盘大于200万
                        effort_anomaly > self.config.v_reversal_dump_threshold and
                        micro_cvd_usdt > self.config.v_reversal_counter_threshold and
                        rebound_ratio > self.config.v_reversal_rebound_ratio and
                        self.config.v_reversal_min_rebound < bounce_pct <= self.config.v_reversal_max_rebound
                )

                # 🎯 击发判定 (后续逻辑不变...)
                if cond_absorption or cond_v_reversal:
                    self.state = "IDLE"
                    self._commit_ema_memory()  # 🌟 击发后，波段结束，刻入大脑记忆！
                    self.last_fire_time = current_ts

                    # 我们预设止损位在现价下方 sl_pct% (即 1 - sl_pct)
                    self.last_stop_loss_price = self.current_price * (1 - self.config.sl_pct)
                    self.last_stop_loss_time = current_ts
                    self.max_price_since_stop = self.current_price

                    # 构建信号数据
                    signal_data = {
                        "level": "STRICT",
                        "price": self.current_price,
                        "local_low": self.local_low,
                        "cvd_delta_usdt": recent_cvd_delta_usdt,
                        "micro_cvd": micro_cvd_usdt,
                        "price_diff_pct": bounce_pct,
                        "effort_anomaly": effort_anomaly,  # 🌟 传给科考船
                        "res_anomaly": resistance_anomaly,  # 🌟 传给科考船
                        "ts": current_ts
                    }

                    # 更新MarketContext（如果存在）
                    if self.context:
                        self.context.update_signal(signal_data)

                    return signal_data

                # 🧪 宽口径击发：反弹 0.03% 时，向科考船汇报，但枪口继续死死瞄准！
                elif -recent_cvd_delta_usdt > 1_000_000 and micro_cvd_usdt > self.config.broad_report_threshold and self.config.broad_min_bounce < bounce_pct <= self.config.broad_max_bounce and not self.broad_fired_this_round:
                    self.broad_fired_this_round = True

                    # 构建信号数据
                    signal_data = {
                        "level": "BROAD",
                        "price": self.current_price,
                        "local_low": self.local_low,
                        "cvd_delta_usdt": recent_cvd_delta_usdt,
                        "micro_cvd": micro_cvd_usdt,
                        "price_diff_pct": bounce_pct,
                        "effort_anomaly": effort_anomaly,  # 🌟 传给科考船
                        "res_anomaly": resistance_anomaly,  # 🌟 传给科考船
                        "ts": current_ts
                    }

                    # 更新MarketContext（如果存在）
                    if self.context:
                        self.context.update_signal(signal_data)

                    return signal_data

        return None

    def _commit_ema_memory(self):
        """波段结束时，统一把这波的最大数据结算进大脑，并存档"""
        if self.round_max_effort_m > self.config.memory_update_threshold_m:
            self.avg_wave_effort_m = (self.avg_wave_effort_m * self.config.memory_decay_factor) + (
                    self.round_max_effort_m * self.config.memory_update_factor)
            self.avg_resistance_bps = (self.avg_resistance_bps * self.config.memory_decay_factor) + (
                    self.round_max_resistance * self.config.memory_update_factor)

            # 🌟 每次更新完，立刻写入本地 JSON 文件
            try:
                with open(self.memory_file, 'w') as f:
                    json.dump({
                        "avg_wave_effort_m": self.avg_wave_effort_m,
                        "avg_resistance_bps": self.avg_resistance_bps
                    }, f)
            except Exception as e:
                logger.error(f"⚠️ [记忆存档] 保存失败: {e}")

        # 结算完清零，准备迎接下一次暴跌
        self.round_max_effort_m = 0.0
        self.round_max_resistance = 0.0

    def _load_ema_memory(self):
        """开机时读取上一次的盘感记忆"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r') as f:
                    data = json.load(f)
                    self.avg_wave_effort_m = data.get("avg_wave_effort_m", 10.0)
                    self.avg_resistance_bps = data.get("avg_resistance_bps", 1.5)
                    logger.warning(f"🧠 [记忆读取] 成功恢复盘感！当前大盘砸盘均值: {self.avg_wave_effort_m:.2f}M")
                    return
            except Exception as e:
                logger.error(f"⚠️ [记忆读取] 失败: {e}，将使用默认初始值。")
        else:
            # 如果没有文件，就用默认的偏小初始值
            self.avg_wave_effort_m = 10.0
            self.avg_resistance_bps = 1.5

            with open(self.memory_file, 'w') as f:
                json.dump({
                    "avg_wave_effort_m": self.avg_wave_effort_m,
                    "avg_resistance_bps": self.avg_resistance_bps
                }, f)

    def detect_absorption_wall(self, tick: dict) -> float:
        """🧱 隐形墙探测 (Absorption Wall) - 合约高流动性专用版"""
        if len(self.snapshots) < 2: return 0.0

        snapshot = self.snapshots[-2]
        lowest_since_snap = min(snapshot['min_price'], self.snapshots[-1]['min_price'], self.interval_min_price)
        recent_cvd_delta = (self.cvd - snapshot['cvd']) * self.config.contract_size * self.current_price
        max_drop_pct = (lowest_since_snap - snapshot['price']) / snapshot['price'] * 100

        # 🌟 门槛暴增：20秒内必须爆砸超过配置阈值！
        if recent_cvd_delta < -self.config.wall_threshold_usdt:
            # 价格竟然被死死按住，最大下潜不到配置阈值！
            if max_drop_pct >= -self.config.wall_max_drop_pct:
                logger.warning(
                    f"🧱 [隐形墙] 逆天护盘！硬扛 ${abs(recent_cvd_delta) / 10000:.0f}万 连环砸盘，下潜仅 {max_drop_pct:.3f}%！")
                # 更新MarketContext（如果存在）
                if self.context:
                    self.context.update_of_wall(lowest_since_snap, tick.get('ts'))
                return lowest_since_snap
        return 0.0

    def detect_short_squeeze(self, tick: dict) -> bool:
        """🔥 动能破冰探测 (Short Squeeze) - 合约专用版"""
        if len(self.snapshots) < 1: return False

        snapshot = self.snapshots[-1]
        recent_cvd_delta = (self.cvd - snapshot['cvd']) * self.config.contract_size * self.current_price
        price_change_pct = (self.current_price - snapshot['price']) / snapshot['price'] * 100

        # 🌟 门槛暴增：10秒内多头狂买超过配置阈值，且瞬间撕裂盘口拉升超过配置阈值！
        if recent_cvd_delta > self.config.squeeze_buy_threshold and price_change_pct > self.config.squeeze_price_change:
            # 更新MarketContext（如果存在）
            if self.context:
                self.context.update_of_squeeze(True, tick.get('ts'))
            return True
        return False
