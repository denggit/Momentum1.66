#!/usr/bin/env python3
"""
四号引擎v3.0 仓位保护器
监控仓位状态，提供移动止损、保本保护、风险监控等功能
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import threading

from src.utils.log import get_logger
from src.strategy.triplea.data_structures import PositionState
from src.strategy.triplea.order_manager import OrderManager

logger = get_logger(__name__)


class GuardType(Enum):
    """保护类型"""
    BREAKEVEN = "breakeven"          # 保本保护
    TRAILING_STOP = "trailing_stop"  # 移动止损
    TIME_BASED = "time_based"        # 时间保护
    VOLATILITY = "volatility"        # 波动率保护
    PNL_TARGET = "pnl_target"        # 盈利目标保护


class GuardStatus(Enum):
    """保护状态"""
    ACTIVE = "active"            # 活跃
    TRIGGERED = "triggered"      # 已触发
    CANCELLED = "cancelled"      # 已取消
    EXPIRED = "expired"          # 已过期


@dataclass
class GuardConfig:
    """保护配置"""
    guard_type: GuardType
    enabled: bool = True
    priority: int = 1  # 优先级，数字越小优先级越高
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardEvent:
    """保护事件"""
    guard_id: str
    guard_type: GuardType
    position_id: str
    trigger_price: float
    current_price: float
    timestamp: float
    status: GuardStatus
    metadata: Dict[str, Any] = field(default_factory=dict)


class PositionGuard:
    """仓位保护器

    功能：
    1. 保本保护：价格达到保本点后移动止损到保本点
    2. 移动止损：根据价格移动动态调整止损
    3. 时间保护：持仓时间过长自动平仓
    4. 波动率保护：市场波动率变化时调整风险
    5. 盈利目标保护：达到盈利目标后保护利润
    """

    def __init__(self, order_manager: OrderManager):
        """初始化仓位保护器

        Args:
            order_manager: 订单管理器
        """
        self.order_manager = order_manager

        # 仓位状态
        self.active_positions: Dict[str, PositionState] = {}
        self.position_history: deque[PositionState] = deque(maxlen=1000)

        # 保护配置
        self.guard_configs: Dict[GuardType, GuardConfig] = self._create_default_configs()

        # 活跃保护
        self.active_guards: Dict[str, GuardEvent] = {}
        self.guard_history: deque[GuardEvent] = deque(maxlen=2000)

        # 保护状态
        self.guards_enabled = True
        self.last_check_time = 0.0

        # 统计信息
        self.stats = {
            "total_guards_triggered": 0,
            "breakeven_guards": 0,
            "trailing_stop_guards": 0,
            "time_based_guards": 0,
            "pnl_protected": 0.0,
            "last_protection_time": 0.0
        }

        # 运行状态
        self.running = False
        self.guard_task: Optional[asyncio.Task] = None

        # 锁
        self._lock = threading.Lock()

    def _create_default_configs(self) -> Dict[GuardType, GuardConfig]:
        """创建默认保护配置"""
        return {
            GuardType.BREAKEVEN: GuardConfig(
                guard_type=GuardType.BREAKEVEN,
                enabled=True,
                priority=1,
                params={
                    "activation_pnl_pct": 0.005,  # 0.5%盈利后激活
                    "breakeven_buffer": 0.001,     # 0.1%保本缓冲
                }
            ),

            GuardType.TRAILING_STOP: GuardConfig(
                guard_type=GuardType.TRAILING_STOP,
                enabled=True,
                priority=2,
                params={
                    "activation_pnl_pct": 0.01,    # 1%盈利后激活
                    "trail_distance_pct": 0.005,   # 0.5%跟踪距离
                    "min_trail_pct": 0.002,        # 最小跟踪距离0.2%
                    "max_trail_pct": 0.02,         # 最大跟踪距离2%
                }
            ),

            GuardType.TIME_BASED: GuardConfig(
                guard_type=GuardType.TIME_BASED,
                enabled=True,
                priority=3,
                params={
                    "max_hold_time_seconds": 3600,  # 最大持仓时间1小时
                    "warning_time_seconds": 1800,   # 30分钟警告
                }
            ),

            GuardType.VOLATILITY: GuardConfig(
                guard_type=GuardType.VOLATILITY,
                enabled=True,
                priority=4,
                params={
                    "volatility_threshold": 0.03,   # 3%波动率阈值
                    "adjustment_factor": 0.5,       # 调整系数
                }
            ),

            GuardType.PNL_TARGET: GuardConfig(
                guard_type=GuardType.PNL_TARGET,
                enabled=True,
                priority=5,
                params={
                    "target_pnl_pct": 0.02,         # 2%盈利目标
                    "partial_close_pct": 0.5,       # 达到目标后平仓50%
                }
            ),
        }

    async def start(self):
        """启动仓位保护器"""
        if self.running:
            return

        self.running = True
        self.guard_task = asyncio.create_task(self._guard_loop())

        logger.info("仓位保护器已启动")

    async def stop(self):
        """停止仓位保护器"""
        if not self.running:
            return

        self.running = False

        if self.guard_task:
            self.guard_task.cancel()
            try:
                await self.guard_task
            except asyncio.CancelledError:
                pass

        logger.info("仓位保护器已停止")

    async def _guard_loop(self):
        """保护主循环"""
        while self.running:
            try:
                # 更新仓位状态
                await self._update_position_states()

                # 检查所有保护
                await self._check_all_guards()

                # 清理旧事件
                self._cleanup_old_events()

                # 等待下一次检查
                await asyncio.sleep(1.0)  # 1秒间隔

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"仓位保护循环错误: {e}")
                await asyncio.sleep(5.0)  # 错误时延长等待

    async def _update_position_states(self):
        """更新仓位状态"""
        try:
            # 这里应该从订单管理器获取仓位状态
            # 暂时使用模拟数据
            pass

        except Exception as e:
            logger.error(f"更新仓位状态失败: {e}")

    async def _check_all_guards(self):
        """检查所有保护"""
        if not self.guards_enabled:
            return

        with self._lock:
            for position_id, position in self.active_positions.items():
                # 检查每个仓位的所有保护
                for guard_type, config in self.guard_configs.items():
                    if not config.enabled:
                        continue

                    await self._check_guard(position, guard_type, config)

        self.last_check_time = time.time()

    async def _check_guard(
        self,
        position: PositionState,
        guard_type: GuardType,
        config: GuardConfig
    ):
        """检查单个保护"""
        try:
            if guard_type == GuardType.BREAKEVEN:
                await self._check_breakeven_guard(position, config)

            elif guard_type == GuardType.TRAILING_STOP:
                await self._check_trailing_stop_guard(position, config)

            elif guard_type == GuardType.TIME_BASED:
                await self._check_time_based_guard(position, config)

            elif guard_type == GuardType.VOLATILITY:
                await self._check_volatility_guard(position, config)

            elif guard_type == GuardType.PNL_TARGET:
                await self._check_pnl_target_guard(position, config)

        except Exception as e:
            logger.error(f"检查保护失败 {guard_type.value}: {e}")

    async def _check_breakeven_guard(self, position: PositionState, config: GuardConfig):
        """检查保本保护"""
        try:
            # 计算当前盈利百分比
            current_price = position.current_price
            entry_price = position.entry_price
            direction = position.direction

            if direction == "LONG":
                pnl_pct = (current_price - entry_price) / entry_price
            else:  # SHORT
                pnl_pct = (entry_price - current_price) / entry_price

            # 检查是否达到激活条件
            activation_pnl = config.params.get("activation_pnl_pct", 0.005)
            if pnl_pct < activation_pnl:
                return

            # 检查是否已有保本保护
            guard_id = f"breakeven_{position.position_id}"
            if guard_id in self.active_guards:
                return

            # 计算保本价格
            breakeven_buffer = config.params.get("breakeven_buffer", 0.001)
            if direction == "LONG":
                breakeven_price = entry_price * (1 + breakeven_buffer)
            else:
                breakeven_price = entry_price * (1 - breakeven_buffer)

            # 创建保护事件
            guard_event = GuardEvent(
                guard_id=guard_id,
                guard_type=GuardType.BREAKEVEN,
                position_id=position.position_id,
                trigger_price=breakeven_price,
                current_price=current_price,
                timestamp=time.time(),
                status=GuardStatus.ACTIVE,
                metadata={
                    "entry_price": entry_price,
                    "breakeven_price": breakeven_price,
                    "pnl_pct": pnl_pct,
                    "activation_pnl": activation_pnl
                }
            )

            # 激活保护
            self.active_guards[guard_id] = guard_event
            self.guard_history.append(guard_event)

            logger.info(f"✅ 激活保本保护: 仓位 {position.position_id}")
            logger.info(f"   入场价: {entry_price:.2f}, 保本价: {breakeven_price:.2f}")
            logger.info(f"   当前价: {current_price:.2f}, 盈利: {pnl_pct:.2%}")

            # 这里应该更新仓位的止损价到保本价
            # await self._update_stop_loss(position.position_id, breakeven_price)

        except Exception as e:
            logger.error(f"检查保本保护失败: {e}")

    async def _check_trailing_stop_guard(self, position: PositionState, config: GuardConfig):
        """检查移动止损保护"""
        try:
            # 计算当前盈利百分比
            current_price = position.current_price
            entry_price = position.entry_price
            direction = position.direction

            if direction == "LONG":
                pnl_pct = (current_price - entry_price) / entry_price
            else:  # SHORT
                pnl_pct = (entry_price - current_price) / entry_price

            # 检查是否达到激活条件
            activation_pnl = config.params.get("activation_pnl_pct", 0.01)
            if pnl_pct < activation_pnl:
                return

            guard_id = f"trailing_{position.position_id}"
            trail_distance_pct = config.params.get("trail_distance_pct", 0.005)

            if guard_id not in self.active_guards:
                # 首次激活移动止损
                if direction == "LONG":
                    trigger_price = current_price * (1 - trail_distance_pct)
                else:
                    trigger_price = current_price * (1 + trail_distance_pct)

                guard_event = GuardEvent(
                    guard_id=guard_id,
                    guard_type=GuardType.TRAILING_STOP,
                    position_id=position.position_id,
                    trigger_price=trigger_price,
                    current_price=current_price,
                    timestamp=time.time(),
                    status=GuardStatus.ACTIVE,
                    metadata={
                        "entry_price": entry_price,
                        "trail_distance_pct": trail_distance_pct,
                        "highest_price": current_price if direction == "LONG" else None,
                        "lowest_price": current_price if direction == "SHORT" else None,
                        "pnl_pct": pnl_pct
                    }
                )

                self.active_guards[guard_id] = guard_event
                self.guard_history.append(guard_event)

                logger.info(f"✅ 激活移动止损: 仓位 {position.position_id}")
                logger.info(f"   入场价: {entry_price:.2f}, 移动止损: {trigger_price:.2f}")
                logger.info(f"   当前价: {current_price:.2f}, 盈利: {pnl_pct:.2%}")

            else:
                # 更新移动止损
                guard_event = self.active_guards[guard_id]
                metadata = guard_event.metadata

                if direction == "LONG":
                    # 更新最高价
                    highest_price = metadata.get("highest_price", current_price)
                    highest_price = max(highest_price, current_price)
                    metadata["highest_price"] = highest_price

                    # 计算新的止损价
                    new_stop = highest_price * (1 - trail_distance_pct)
                    if new_stop > guard_event.trigger_price:
                        guard_event.trigger_price = new_stop
                        guard_event.current_price = current_price
                        guard_event.timestamp = time.time()

                        logger.info(f"📈 更新移动止损: {new_stop:.2f} (最高价: {highest_price:.2f})")

                else:  # SHORT
                    # 更新最低价
                    lowest_price = metadata.get("lowest_price", current_price)
                    lowest_price = min(lowest_price, current_price)
                    metadata["lowest_price"] = lowest_price

                    # 计算新的止损价
                    new_stop = lowest_price * (1 + trail_distance_pct)
                    if new_stop < guard_event.trigger_price:
                        guard_event.trigger_price = new_stop
                        guard_event.current_price = current_price
                        guard_event.timestamp = time.time()

                        logger.info(f"📉 更新移动止损: {new_stop:.2f} (最低价: {lowest_price:.2f})")

                # 检查是否触发止损
                if (direction == "LONG" and current_price <= guard_event.trigger_price) or \
                   (direction == "SHORT" and current_price >= guard_event.trigger_price):

                    guard_event.status = GuardStatus.TRIGGERED
                    self.stats["total_guards_triggered"] += 1
                    self.stats["trailing_stop_guards"] += 1
                    self.stats["last_protection_time"] = time.time()

                    logger.info(f"🛑 移动止损触发: 仓位 {position.position_id}")
                    logger.info(f"   触发价: {guard_event.trigger_price:.2f}, 当前价: {current_price:.2f}")

                    # 这里应该触发平仓
                    # await self._close_position(position.position_id, "移动止损触发")

        except Exception as e:
            logger.error(f"检查移动止损保护失败: {e}")

    async def _check_time_based_guard(self, position: PositionState, config: GuardConfig):
        """检查时间保护"""
        try:
            position_time = time.time() - position.entry_time
            max_hold_time = config.params.get("max_hold_time_seconds", 3600)
            warning_time = config.params.get("warning_time_seconds", 1800)

            guard_id = f"time_{position.position_id}"

            if position_time > max_hold_time:
                # 超过最大持仓时间，触发保护
                if guard_id not in self.active_guards:
                    guard_event = GuardEvent(
                        guard_id=guard_id,
                        guard_type=GuardType.TIME_BASED,
                        position_id=position.position_id,
                        trigger_price=position.current_price,
                        current_price=position.current_price,
                        timestamp=time.time(),
                        status=GuardStatus.TRIGGERED,
                        metadata={
                            "entry_time": position.entry_time,
                            "hold_time_seconds": position_time,
                            "max_hold_time": max_hold_time
                        }
                    )

                    self.active_guards[guard_id] = guard_event
                    self.guard_history.append(guard_event)

                    logger.warning(f"⏰ 时间保护触发: 仓位 {position.position_id} 持仓超过{max_hold_time/60:.0f}分钟")
                    logger.warning(f"   入场时间: {position.entry_time}, 持仓时间: {position_time/60:.1f}分钟")

                    # 这里应该触发平仓或警告
                    # await self._close_position(position.position_id, "持仓时间过长")

            elif position_time > warning_time and guard_id not in self.active_guards:
                # 超过警告时间，发送警告
                guard_event = GuardEvent(
                    guard_id=guard_id,
                    guard_type=GuardType.TIME_BASED,
                    position_id=position.position_id,
                    trigger_price=position.current_price,
                    current_price=position.current_price,
                    timestamp=time.time(),
                    status=GuardStatus.ACTIVE,
                    metadata={
                        "entry_time": position.entry_time,
                        "hold_time_seconds": position_time,
                        "warning_time": warning_time
                    }
                )

                self.active_guards[guard_id] = guard_event

                logger.info(f"⏰ 时间保护警告: 仓位 {position.position_id} 持仓超过{warning_time/60:.0f}分钟")

        except Exception as e:
            logger.error(f"检查时间保护失败: {e}")

    async def _check_volatility_guard(self, position: PositionState, config: GuardConfig):
        """检查波动率保护"""
        try:
            # 这里应该获取市场波动率数据
            # 暂时跳过实现
            pass

        except Exception as e:
            logger.error(f"检查波动率保护失败: {e}")

    async def _check_pnl_target_guard(self, position: PositionState, config: GuardConfig):
        """检查盈利目标保护"""
        try:
            # 计算当前盈利百分比
            current_price = position.current_price
            entry_price = position.entry_price
            direction = position.direction

            if direction == "LONG":
                pnl_pct = (current_price - entry_price) / entry_price
            else:  # SHORT
                pnl_pct = (entry_price - current_price) / entry_price

            target_pnl = config.params.get("target_pnl_pct", 0.02)
            if pnl_pct < target_pnl:
                return

            guard_id = f"pnl_target_{position.position_id}"
            if guard_id in self.active_guards:
                return

            # 触发盈利目标保护
            guard_event = GuardEvent(
                guard_id=guard_id,
                guard_type=GuardType.PNL_TARGET,
                position_id=position.position_id,
                trigger_price=position.current_price,
                current_price=position.current_price,
                timestamp=time.time(),
                status=GuardStatus.TRIGGERED,
                metadata={
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "pnl_pct": pnl_pct,
                    "target_pnl": target_pnl
                }
            )

            self.active_guards[guard_id] = guard_event
            self.guard_history.append(guard_event)

            self.stats["total_guards_triggered"] += 1
            self.stats["pnl_protected"] += position.position_size * (current_price - entry_price)
            self.stats["last_protection_time"] = time.time()

            logger.info(f"🎯 盈利目标保护触发: 仓位 {position.position_id}")
            logger.info(f"   盈利: {pnl_pct:.2%}, 目标: {target_pnl:.2%}")

            # 这里可以执行部分平仓
            # partial_close_pct = config.params.get("partial_close_pct", 0.5)
            # await self._partial_close_position(position.position_id, partial_close_pct, "达到盈利目标")

        except Exception as e:
            logger.error(f"检查盈利目标保护失败: {e}")

    def _cleanup_old_events(self):
        """清理旧事件"""
        current_time = time.time()
        to_remove = []

        for guard_id, guard in self.active_guards.items():
            # 如果事件已触发且超过1小时，或已取消/过期
            if (guard.status in [GuardStatus.TRIGGERED, GuardStatus.CANCELLED, GuardStatus.EXPIRED] and
                current_time - guard.timestamp > 3600):
                to_remove.append(guard_id)

        for guard_id in to_remove:
            del self.active_guards[guard_id]

    def add_position(self, position: PositionState):
        """添加仓位到监控"""
        with self._lock:
            self.active_positions[position.position_id] = position
            self.position_history.append(position)

            logger.info(f"📊 开始监控仓位: {position.position_id}")

    def remove_position(self, position_id: str):
        """移除仓位监控"""
        with self._lock:
            if position_id in self.active_positions:
                del self.active_positions[position_id]

                # 取消该仓位的所有保护
                to_remove = [
                    guard_id for guard_id, guard in self.active_guards.items()
                    if guard.position_id == position_id
                ]

                for guard_id in to_remove:
                    guard = self.active_guards[guard_id]
                    guard.status = GuardStatus.CANCELLED
                    # 不立即删除，等待清理

                logger.info(f"📊 停止监控仓位: {position_id} (取消{len(to_remove)}个保护)")

    def update_position_price(self, position_id: str, current_price: float):
        """更新仓位价格"""
        with self._lock:
            if position_id in self.active_positions:
                self.active_positions[position_id].current_price = current_price

    def get_position_guards(self, position_id: str) -> List[GuardEvent]:
        """获取仓位的保护事件"""
        return [
            guard for guard in self.active_guards.values()
            if guard.position_id == position_id
        ]

    def get_active_guards(self) -> List[GuardEvent]:
        """获取活跃保护"""
        return [
            guard for guard in self.active_guards.values()
            if guard.status == GuardStatus.ACTIVE
        ]

    def get_guard_history(self, limit: int = 100) -> List[GuardEvent]:
        """获取保护历史"""
        return list(self.guard_history)[-limit:]

    def enable_guard_type(self, guard_type: GuardType, enabled: bool = True):
        """启用/禁用保护类型"""
        if guard_type in self.guard_configs:
            self.guard_configs[guard_type].enabled = enabled
            status = "启用" if enabled else "禁用"
            logger.info(f"🛡️  {status}保护类型: {guard_type.value}")

    def update_guard_config(self, guard_type: GuardType, params: Dict[str, Any]):
        """更新保护配置"""
        if guard_type in self.guard_configs:
            self.guard_configs[guard_type].params.update(params)
            logger.info(f"⚙️  更新保护配置: {guard_type.value}")

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        active_positions = len(self.active_positions)
        active_guards = len(self.get_active_guards())

        return {
            **self.stats,
            "active_positions": active_positions,
            "active_guards": active_guards,
            "guards_enabled": self.guards_enabled,
            "last_check_time": self.last_check_time,
            "guard_configs": {
                guard_type.value: {
                    "enabled": config.enabled,
                    "priority": config.priority
                }
                for guard_type, config in self.guard_configs.items()
            }
        }

    def enable_all_guards(self):
        """启用所有保护"""
        self.guards_enabled = True
        logger.info("🛡️  启用所有仓位保护")

    def disable_all_guards(self):
        """禁用所有保护"""
        self.guards_enabled = False
        logger.info("🛡️  禁用所有仓位保护")


async def test_position_guard():
    """测试仓位保护器"""
    print("🧪 测试仓位保护器")
    print("=" * 60)

    from src.strategy.triplea.data_structures import PositionState

    # 创建模拟的订单管理器
    class MockOrderManager:
        pass

    order_manager = MockOrderManager()

    # 创建仓位保护器
    position_guard = PositionGuard(order_manager)

    # 测试1：创建模拟仓位
    print("\n📊 测试1：创建模拟仓位")
    position = PositionState(
        position_id="test_position_001",
        symbol="ETH-USDT-SWAP",
        direction="LONG",
        entry_price=3000.0,
        current_price=3020.0,
        position_size=0.1,
        entry_time=time.time() - 1200,  # 20分钟前入场
        stop_loss_price=2998.0,
        take_profit_price=3012.0,
        unrealized_pnl=20.0,
        realized_pnl=0.0
    )

    position_guard.add_position(position)

    # 测试2：启动保护器
    print("\n📊 测试2：启动保护器")
    await position_guard.start()

    # 等待一段时间
    print("等待保护检查...")
    await asyncio.sleep(2)

    # 测试3：获取活跃保护
    print("\n📊 测试3：获取活跃保护")
    active_guards = position_guard.get_active_guards()
    for guard in active_guards:
        print(f"  • {guard.guard_type.value}: {guard.position_id} (触发价: {guard.trigger_price:.2f})")

    # 测试4：更新价格触发移动止损
    print("\n📊 测试4：更新价格测试移动止损")
    position_guard.update_position_price("test_position_001", 3030.0)
    await asyncio.sleep(1)

    position_guard.update_position_price("test_position_001", 3015.0)
    await asyncio.sleep(1)

    # 测试5：获取统计信息
    print("\n📊 测试5：获取统计信息")
    stats = position_guard.get_statistics()
    for key, value in stats.items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k, v in value.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {key}: {value}")

    # 测试6：获取保护历史
    print("\n📊 测试6：保护历史")
    history = position_guard.get_guard_history(5)
    for guard in history:
        print(f"  • {guard.guard_type.value}: {guard.status.value} at {guard.trigger_price:.2f}")

    # 测试7：停止保护器
    print("\n📊 测试7：停止保护器")
    await position_guard.stop()

    print("\n✅ 仓位保护器测试完成")


if __name__ == "__main__":
    asyncio.run(test_position_guard())