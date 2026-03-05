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
                'cvd': self.cvd
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

            # 动作 C：绝地反击！(微观价格开始反弹，计算从【绝对坑底】到【此时此刻】的主动买盘量)
            else:
                micro_cvd_usdt = (self.cvd - self.local_low_cvd) * CONTRACT_SIZE * self.current_price
                bounce_pct = (self.current_price - self.local_low) / self.local_low * 100

                # ⚔️ 严口径击发：反弹拐头 > 0.05% ...
                if micro_cvd_usdt > 150_000 and 0.05 < bounce_pct <= 0.20 and recent_cvd_delta_usdt < -5_000_000:
                    self.state = "IDLE"  # 开火后重置状态机
                    self.last_fire_time = current_ts

                    # 🌟 核心补丁：在这里更新拦截器的基准值！
                    # 我们预设止损位在现价下方 0.05% (即 0.9995)
                    self.last_stop_loss_price = self.current_price * 0.9995
                    self.last_stop_loss_time = current_ts
                    self.max_price_since_stop = self.current_price  # 重置反弹高点

                    return {
                        "level": "STRICT",
                        "price": self.current_price,  # 触发时的现价
                        "local_low": self.local_low,  # 🌟 真正探明的局部最低价
                        "cvd_delta_usdt": recent_cvd_delta_usdt,
                        "micro_cvd": micro_cvd_usdt,
                        "price_diff_pct": bounce_pct,  # 为了兼容之前的 tracker，键名依然叫 price_diff，但存的是百分比
                        "ts": current_ts
                    }

                # 🧪 宽口径击发：反弹 0.02% 时，向科考船汇报，但枪口继续死死瞄准！
                elif micro_cvd_usdt > 30_000 and 0.02 < bounce_pct <= 0.30 and not self.broad_fired_this_round:
                    # 🚨 注意：这里千万千万不能有 self.state = "IDLE" ！！！
                    # 只要不 IDLE，系统下一秒还会继续监控是否能达到 STRICT！
                    
                    self.broad_fired_this_round = True # 上锁，避免这一波反弹重复发废话
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
