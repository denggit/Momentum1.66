import time
from typing import Dict, Optional

from src.strategy.triplea.data_structures import (
    TripleAEngineConfig, NormalizedTick
)
from src.strategy.triplea.state_machine import TripleAStateMachine, TripleAState
from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleASignalGenerator:
    """四号引擎v3.0 信号生成器（集成状态机）

    实现完整的5状态模型，集成LVN检测、CVD分析、波动率压缩检测等核心算法。
    保持与现有orchestrator.py接口100%兼容。
    """

    def __init__(self, symbol: str = "ETH-USDT-SWAP", is_shadow: bool = False):
        self.symbol = symbol
        self.is_shadow = is_shadow

        # 创建默认配置（基于300U小资金优化）
        self.config = TripleAEngineConfig()

        # 调整配置参数以适应小资金
        self.config.risk_manager.account_size_usdt = 300.0  # 300U账户
        self.config.risk_manager.max_risk_per_trade_pct = 5.0  # 5%单笔风险
        self.config.risk_manager.daily_loss_limit_pct = 5.0  # 5%日损失限制
        self.config.risk_manager.fee_rate_taker = 0.0005  # 双边0.1%手续费（0.05%每边）
        self.config.risk_manager.min_rr_ratio = 2.0  # 盈亏比至少2:1
        self.config.risk_manager.min_tp_distance_pct = 0.2  # 止盈距离至少0.2%

        # 对于影子引擎，可以放宽参数以便收集更多数据
        if is_shadow:
            self.config.risk_manager.max_risk_per_trade_pct = 2.0  # 影子引擎降低风险
            self.config.risk_manager.min_rr_ratio = 1.5  # 影子引擎降低盈亏比要求

        # 初始化状态机（核心算法引擎）
        self.state_machine = TripleAStateMachine(self.config)

        # 兼容性属性（供orchestrator和轨迹矿工访问）
        self.status = "IDLE"  # 兼容性状态（映射到状态机状态）
        self.tradable_zones = []  # 战术地图（8小时）
        self.macro_zones = []  # 战略地图（24小时）
        self.profile = {}  # 当前profile数据

        # 全局统计（供轨迹矿工访问）
        self.global_cvd = 0.0  # 全局CVD值
        self.global_volume = 0.0  # 全局成交量

        # 订单状态（兼容性）
        self.current_sl = 0.0
        self.current_tp = 0.0

        # 微跟踪器（兼容性）
        self.micro_tracker = {
            "direction": None,  # "LONG" 或 "SHORT"
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "allowed_direction": "ANY",
            "locked_zone_key": None
        }

        # 性能监控
        self.processed_ticks = 0
        self.last_signal_time = 0.0

        logger.info(f"TripleASignalGenerator 初始化完成 (symbol={symbol}, is_shadow={is_shadow})")

    def _process_zones(self, raw_zones):
        """内部辅助：安全拷贝阵地列表（兼容性方法）"""
        safe_tradable_zones = []
        for zone in raw_zones:
            safe_zone = zone.copy()
            safe_tradable_zones.append(safe_zone)
        return safe_tradable_zones

    def update_maps(self, short_profile: Dict, long_profile: Dict):
        """🚀 双轨雷达接收：短线管进场，长线管止盈（兼容性方法）

        更新战术地图和战略地图，供轨迹矿工和状态机使用。
        """
        self.profile = short_profile

        # 1. 战术地图 (8小时)：日常打仗、A1吸收全靠它
        self.tradable_zones = self._process_zones(short_profile.get('tradable_zones', []))

        # 2. 战略地图 (24小时)：专门用来寻找极高盈亏比的止盈点
        self.macro_zones = self._process_zones(long_profile.get('tradable_zones', []))

        # 3. 更新全局统计（供轨迹矿工访问）
        self.global_cvd = short_profile.get('global_cvd', 0.0)
        self.global_volume = short_profile.get('global_volume', 0.0)

        logger.debug(f"地图已更新: {len(self.tradable_zones)}个战术区域, {len(self.macro_zones)}个战略区域")

    def process_tick(self, tick: Dict) -> Optional[Dict]:
        """处理单个Tick，驱动状态机并返回交易信号（兼容性接口）

        Args:
            tick: 包含price, size, side, ts字段的字典

        Returns:
            交易信号字典（如果状态机生成信号），否则返回None
        """
        self.processed_ticks += 1

        try:
            # 1. 将orchestrator格式的tick转换为状态机格式
            normalized_tick = self._convert_to_normalized_tick(tick)

            # 2. 更新全局统计（供轨迹矿工访问）
            self._update_global_stats(tick)

            # 3. 驱动状态机处理Tick
            state_machine_signal = self.state_machine.process_tick(normalized_tick)

            # 4. 同步状态机状态到兼容性状态
            self._sync_state_from_state_machine()

            # 5. 如果状态机生成信号，转换为兼容性格式
            if state_machine_signal:
                return self._convert_state_machine_signal(state_machine_signal)

            return None

        except Exception as e:
            logger.error(f"处理Tick时出错: {e}", exc_info=True)
            return None

    def _convert_to_normalized_tick(self, tick_dict: Dict) -> NormalizedTick:
        """将orchestrator tick字典转换为NormalizedTick"""
        # 原始tick格式：{'price': 3000.0, 'size': 1.0, 'side': 'buy', 'ts': 1234567890000}
        # side: 'buy' -> 1, 'sell' -> -1
        side_int = 1 if tick_dict.get('side', '').lower() == 'buy' else -1

        # 时间戳：orchestrator使用毫秒，NormalizedTick使用纳秒
        ts_ms = tick_dict.get('ts', tick_dict.get('timestamp', int(time.time() * 1000)))
        ts_ns = ts_ms * 1_000_000  # 毫秒转纳秒

        return NormalizedTick(
            ts=ts_ns,
            px=float(tick_dict['price']),
            sz=float(tick_dict['size']),
            side=side_int
        )

    def _update_global_stats(self, tick_dict: Dict):
        """更新全局统计（供轨迹矿工访问）"""
        # 简单累加CVD（买+，卖-）
        if tick_dict.get('side', '').lower() == 'buy':
            self.global_cvd += float(tick_dict['size'])
        else:
            self.global_cvd -= float(tick_dict['size'])

        # 累加成交量
        self.global_volume += float(tick_dict['size'])

    def _sync_state_from_state_machine(self):
        """同步状态机状态到兼容性状态"""
        state_machine_state = self.state_machine.context.current_state

        # 映射状态机状态到兼容性状态
        state_mapping = {
            TripleAState.IDLE: "IDLE",
            TripleAState.MONITORING: "IDLE",  # 监控状态对外显示为IDLE
            TripleAState.CONFIRMED: "IDLE",  # 确认状态对外显示为IDLE
            TripleAState.ACCUMULATING: "IDLE",  # 积累状态对外显示为IDLE
            TripleAState.POSITION: "LONG" if self.state_machine.context.trade_direction == "LONG" else "SHORT"
        }

        self.status = state_mapping.get(state_machine_state, "IDLE")

        # 如果处于持仓状态，更新止损止盈价格
        if state_machine_state == TripleAState.POSITION:
            self.current_sl = self.state_machine.context.stop_loss_price
            self.current_tp = self.state_machine.context.take_profit_price

            # 更新micro_tracker
            self.micro_tracker.update({
                "direction": self.state_machine.context.trade_direction,
                "entry_price": self.state_machine.context.entry_price,
                "stop_loss": self.state_machine.context.stop_loss_price,
                "take_profit": self.state_machine.context.take_profit_price
            })

    def _convert_state_machine_signal(self, state_machine_signal: Dict) -> Dict:
        """将状态机信号转换为orchestrator兼容格式

        状态机信号格式：
        {
            'action': 'OPEN_LONG' 或 'OPEN_SHORT',
            'reason': 'AGGRESSION_SIGNAL',
            'price': 3000.0,
            'stop_loss': 2998.0,
            'take_profit': 3012.0,
            'quantity': 0.1,
            ...
        }

        orchestrator期望格式：
        {
            'action': 'BUY' 或 'SELL',
            'reason': 'TRIPLE_A_COMPLETE',
            'entry_price': 3000.0,
            'take_profit': 3012.0,
            'stop_loss': 2998.0,
            ...
        }
        """
        # 提取状态机信号中的关键信息
        action = state_machine_signal.get('action', '')
        price = state_machine_signal.get('price', 0.0)
        stop_loss = state_machine_signal.get('stop_loss', 0.0)
        take_profit = state_machine_signal.get('take_profit', 0.0)
        quantity = state_machine_signal.get('quantity', 0.0)

        # 映射action
        action_mapping = {
            'OPEN_LONG': 'BUY',
            'OPEN_SHORT': 'SELL',
            'CLOSE_LONG': 'CLOSE_LONG',
            'CLOSE_SHORT': 'CLOSE_SHORT'
        }

        mapped_action = action_mapping.get(action, action)

        # 构建兼容性信号
        compatible_signal = {
            'action': mapped_action,
            'reason': 'TRIPLE_A_COMPLETE',  # orchestrator期望的原因
            'entry_price': price,
            'take_profit': take_profit,
            'stop_loss': stop_loss,
            'price': price,  # 保持向后兼容
            'quantity': quantity,
            'timestamp': time.time(),
            'state_machine_signal': state_machine_signal  # 包含原始信号供调试
        }

        logger.info(f"✅ 生成兼容性信号: {compatible_signal['action']} @ {compatible_signal['entry_price']:.2f}")
        logger.info(f"   止损: {compatible_signal['stop_loss']:.2f}, 止盈: {compatible_signal['take_profit']:.2f}")

        return compatible_signal

    def _reset_to_idle(self):
        """重置状态到IDLE（兼容性方法）"""
        # 状态机已经处理状态重置，这里只需更新兼容性状态
        self.status = "IDLE"
        self.current_sl = 0.0
        self.current_tp = 0.0

        # 重置micro_tracker
        self.micro_tracker.update({
            "direction": None,
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0
        })

        self._log_debug("🧹 状态已重置，等待新的资金入场...")

    def _manage_position_by_tick(self, tick: Dict) -> Optional[Dict]:
        """
        持仓飞行模式 (IN_POSITION) - 兼容性方法

        注意：状态机已经处理持仓管理，此方法仅用于兼容性。
        如果状态机处于POSITION状态，将由状态机处理止损止盈。
        此方法只在兼容性状态为LONG/SHORT但状态机未处于POSITION时调用。
        """
        # 如果状态机处于POSITION状态，由状态机处理
        if self.state_machine.context.current_state == TripleAState.POSITION:
            return None

        # 兼容性逻辑（仅当状态机未运行时使用）
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
