from collections import deque
from typing import Dict, Optional

from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleASignalGenerator:
    def __init__(self, symbol: str = "ETH-USDT-SWAP", is_shadow: bool = False):
        self.symbol = symbol
        self.is_shadow = is_shadow
        # 基础状态
        self.status = "IDLE"
        self.tradable_zones = []
        self.macro_zones = []
        self.profile = {}

        # 数据流基础设施 - 精简版，只保留CVD检测所需
        # 不再维护网格系统和滚动窗口，新算法基于CVD突增检测

        # 简单状态跟踪（待新算法填充）
        self.micro_tracker = {
            "direction": None,  # "LONG" 或 "SHORT"
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0
        }

        # 订单状态
        self.current_sl = 0.0
        self.current_tp = 0.0

        # CVD检测基础设施 - 精简框架，新算法将重新实现
        self.current_cvd_15s = 0.0  # 当前CVD累加值（供参考）
        self.cvd_hourly_avg = 0.0   # 历史CVD平均值（供参考）
        self.cvd_spike_threshold = 3.0  # 突增阈值（供参考）

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

        # 3. 网格自适应已移除，新算法不需要网格系统
        # 保留区域信息供新算法使用

    def process_tick(self, tick: Dict) -> Optional[Dict]:
        price = tick['price']
        size = tick['size']
        current_time = int(tick.get('ts', tick.get('timestamp'))) / 1000.0

        # 1. 大管家极速更新底层 O(1) 数据流
        self._update_rolling_data(price, size, tick['side'], current_time)

        # 2. 状态机路由 (State Machine Routing)
        if self.status in ["LONG", "SHORT"]:
            return self._manage_position_by_tick(tick)
        elif self.status == "IDLE":
            # TODO: 新算法将在这里实现IDLE状态检测逻辑
            return self._handle_idle(price)

        # A1/A2/A3状态处理已移除，将由新算法重新实现
        return None

    def _handle_idle(self, price: float) -> Optional[Dict]:
        """IDLE状态处理（待新算法实现）"""
        # TODO: 新算法将在这里实现IDLE状态检测逻辑
        # 基于15秒CVD窗口和1小时平均CVD值的突增检测
        return None

    # ==========================================
    # 🆕 多模式A1检测框架（已移除，将由新算法重新实现）
    # ==========================================

    def _handle_absorption(self, price: float, current_time: float) -> Optional[Dict]:
        """A1吸收阶段处理（待新算法实现）"""
        # TODO: 新算法将在这里实现A1吸收阶段检测逻辑
        return None

    def _handle_accumulation(self, price: float, current_time: float) -> Optional[Dict]:
        """A2积累阶段处理（待新算法实现）"""
        # TODO: 新算法将在这里实现A2积累阶段检测逻辑
        return None

    def _handle_aggression(self, tick: Dict) -> Optional[Dict]:
        """A3攻击阶段处理（待新算法实现）"""
        # TODO: 新算法将在这里实现A3攻击阶段检测逻辑
        return None

    def _update_rolling_data(self, price: float, size: float, side: str, current_time: float):
        # 🆕 时间框架数据维护已移除，新算法将自行管理
        # 只保留简单的CVD累加供参考
        tick_delta = size if side == 'buy' else -size
        self.current_cvd_15s += tick_delta  # 注意：这里没有窗口重置，仅供新算法参考
        # cvd_hourly_avg 等字段由新算法维护

    def _reset_to_idle(self):
        # 重置状态到IDLE，保留阵地钢印
        preserved_dir = self.micro_tracker.get('allowed_direction', 'ANY') if hasattr(self, 'micro_tracker') else 'ANY'
        preserved_key = self.micro_tracker.get('locked_zone_key', None) if hasattr(self, 'micro_tracker') else None

        self.status = "IDLE"
        self.target_zone = None
        self.absorption_start_time = 0.0
        self.micro_tracker = {
            "direction": None,  # "LONG" 或 "SHORT"
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "allowed_direction": preserved_dir,  # 继承方向
            "locked_zone_key": preserved_key     # 继承坐标
        }

        self._log_debug("🧹 状态已重置，等待新的资金入场...")

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
