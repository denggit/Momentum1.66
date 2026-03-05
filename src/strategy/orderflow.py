import time
from collections import deque


class OrderFlowMath:
    def __init__(self):
        self.cvd = 0.0
        self.current_price = 0.0

        # 仅仅作为历史锚点使用，不再用于遍历寻找最低价（大大减轻 CPU 压力）
        self.snapshots = deque(maxlen=30)  # 只存过去 5 分钟 (30个10秒快照)
        self.last_snapshot_time = time.time()

        # ==========================================
        # 🔫 极速状态机 (Event-Driven State Machine)
        # ==========================================
        self.state = "IDLE"  # 状态：IDLE (空闲) -> ARMED (上膛)
        self.armed_time = 0.0  # 上膛的时间戳
        self.local_low = 0.0  # 上膛后的局部最低价 (坑底价格)
        self.local_low_cvd = 0.0  # 局部最低价那一瞬间的 CVD 值

        # 防止连续开火的冷却锁
        self.last_fire_time = 0.0
        self.last_stop_loss_price = 0.0
        self.last_stop_loss_time = 0.0
        self.max_price_since_stop = 0.0  # 止损后的最高反弹价

        # 🌟 新增：这轮探底是否已经报告过宽口径了
        self.broad_fired_this_round = False

        # ==========================================
        # 🧠 动态流动性记忆 (Dynamic Liquidity Memory)
        # ==========================================
        # 初始给一个偏小的默认值，系统跑几分钟后会通过 EMA 自动修正为真实的盘口数据
        self.avg_wave_effort_m = 5.0  # 近期平均波段砸盘资金 (单位: 百万 USDT)
        self.avg_resistance_bps = 0.5  # 近期平均推进阻力 (单位: 百万 USDT / bps)

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

        # 2. 维护历史锚点 (仅为了获取"3分钟前"的CVD基准，极低频操作)
        if current_ts - self.last_snapshot_time >= 10:
            self.snapshots.append({
                'ts': current_ts,
                'cvd': self.cvd,
                'price': self.current_price  # 🌟 新增：记录当时的现价，用于计算真实跌幅
            })
            self.last_snapshot_time = current_ts

        # 刚开机，数据不够 3 分钟 (18个快照)，保持沉默防飞刀
        if len(self.snapshots) < 18:
            return None

        # ==========================================
        # 🧠 毫秒级状态机逻辑开始
        # ==========================================
        # 获取 3 分钟前的 CVD 作为基准
        snapshot_3m_ago = self.snapshots[-18]
        CONTRACT_SIZE = 0.1  # ETH 每张合约 0.1 个币，如果你做大饼记得在配置里改这里
        recent_cvd_delta_usdt = (self.cvd - snapshot_3m_ago['cvd']) * CONTRACT_SIZE * self.current_price

        # 阶段 1：触发上膛 (ARMED)
        # 只要 3 分钟内被砸了 100 万刀，系统立刻进入备战状态，死死盯住盘口
        if self.state == "IDLE":
            if recent_cvd_delta_usdt < -1_000_000 and (current_ts - self.last_fire_time > 300):
                self.state = "ARMED"
                self.armed_time = current_ts
                self.local_low = self.current_price
                self.local_low_cvd = self.cvd
                return None

        # 阶段 2：让子弹飞 (Tracking Bottom) & 击发 (FIRE)
        elif self.state == "ARMED":
            # ==========================================
            # 🛡️ 智能空间拦截 (只有满足以下所有条件才拦截)
            # ==========================================
            if self.last_stop_loss_price > 0:
                is_recent = (current_ts - self.last_stop_loss_time) < 900  # 15分钟内算近期
                is_not_dipped = self.current_price >= self.last_stop_loss_price  # 没跌破前止损位
                is_no_rebound = self.max_price_since_stop < self.last_stop_loss_price * 1.005  # 没反弹超过0.5%

                if is_recent and is_not_dipped and is_no_rebound:
                    # 只有在 15分钟内、没跌破新低、且中间没像样反弹的情况下，才认为是“高频磨损”，拦截！
                    return None

            # 动作 A：价格还在创新低！说明没跌完，绝对不开火！不断下移防线！
            if self.current_price < self.local_low:
                self.local_low = self.current_price
                self.local_low_cvd = self.cvd  # 更新坑底的 CVD 坐标
                self.armed_time = current_ts  # 刷新上膛时间，重新倒计时
                self.broad_fired_this_round = False  # 🌟 创新低了，锁解开，允许重新探测

            # 动作 B：解除武装 (如果 120 秒内都在坑底横盘，没有买盘反抽，说明死水一潭，放弃)
            elif current_ts - self.armed_time > 120:
                self.state = "IDLE"

            # 动作 C：绝地反击或极限吸收！
            else:
                micro_cvd_usdt = (self.cvd - self.local_low_cvd) * CONTRACT_SIZE * self.current_price
                bounce_pct = (self.current_price - self.local_low) / self.local_low * 100

                # 1. 计算本次波段的真实物理量
                price_3m_ago = snapshot_3m_ago['price']
                effort_m = abs(recent_cvd_delta_usdt) / 1_000_000
                price_drop_pct = (price_3m_ago - self.local_low) / price_3m_ago * 100
                safe_drop = max(price_drop_pct, 0.005)
                current_resistance = effort_m / (safe_drop * 100)

                # ==========================================
                # 🧠 核心：让系统“学习”并更新动态基准
                # ==========================================
                # 只有当空头砸了超过 200万 刀时，这个波段的阻力才有统计学意义 (过滤极小波动)
                if effort_m > 2.0:
                    # 采用 EMA(指数移动平均) 更新，Alpha=0.1 意味着最新的波段占 10% 权重，历史占 90%
                    self.avg_wave_effort_m = (self.avg_wave_effort_m * 0.9) + (effort_m * 0.1)
                    self.avg_resistance_bps = (self.avg_resistance_bps * 0.9) + (current_resistance * 0.1)

                # ==========================================
                # 🧊 轨 0：真·动态极限吸收
                # ==========================================
                # 此时使用的 self.avg_wave_effort_m 是系统刚刚从盘面里“闻”出来的真实均值！
                effort_anomaly = effort_m / self.avg_wave_effort_m
                resistance_anomaly = current_resistance / self.avg_resistance_bps

                # 冰山吸收条件：
                # 1. 空头发力了 (砸盘资金是近期平均波段的 1.5 倍以上)
                # 2. 价格跌不动 (< 0.06%)
                # 3. 冰山显灵：推进阻力是当前正常水平的 4.0 倍以上！
                cond_absorption = (
                        effort_anomaly > 1.5 and
                        price_drop_pct < 0.06 and
                        resistance_anomaly > 4.0
                )

                # ==========================================
                # 🔥 轨 1 & 轨 2：动态 V型反转
                # ==========================================
                cond_v_reversal = (
                        effort_anomaly > 1.2 and
                        micro_cvd_usdt > 500_000 and
                        0.12 < bounce_pct <= 0.35
                )

                # 🎯 击发判定 (后续逻辑不变...)
                if cond_absorption or cond_v_reversal:
                    self.state = "IDLE"
                    self.last_fire_time = current_ts

                    # 我们预设止损位在现价下方 0.3% (即 0.997)
                    self.last_stop_loss_price = self.current_price * 0.997
                    self.last_stop_loss_time = current_ts
                    self.max_price_since_stop = self.current_price

                    return {
                        "level": "STRICT",
                        "price": self.current_price,
                        "local_low": self.local_low,
                        "cvd_delta_usdt": recent_cvd_delta_usdt,
                        "micro_cvd": micro_cvd_usdt,
                        "price_diff_pct": bounce_pct,
                        "ts": current_ts
                    }

                # 🧪 宽口径击发：反弹 0.02% 时，向科考船汇报，但枪口继续死死瞄准！
                elif micro_cvd_usdt > 30_000 and 0.02 < bounce_pct <= 0.30 and not self.broad_fired_this_round:
                    self.broad_fired_this_round = True
                    return {
                        "level": "BROAD",
                        "price": self.current_price,
                        "local_low": self.local_low,
                        "cvd_delta_usdt": recent_cvd_delta_usdt,
                        "micro_cvd": micro_cvd_usdt,
                        "price_diff_pct": bounce_pct,
                        "ts": current_ts
                    }

        return None
