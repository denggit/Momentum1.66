#!/usr/bin/env python3
"""
四号引擎v3.0 紧急情况处理器
处理连接丢失、市场异常、仓位风险过高等紧急情况
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, TYPE_CHECKING

from src.strategy.triplea.execution.okx_executor import OKXOrderExecutor
from src.strategy.triplea.execution.order_manager import OrderManager
from src.utils.log import get_logger

if TYPE_CHECKING:
    from src.strategy.triplea.risk.real_time_risk_monitor import RiskAlert, RiskLevel

logger = get_logger(__name__)


class EmergencyType(Enum):
    """紧急情况类型"""
    CONNECTION_LOST = "connection_lost"  # 连接丢失
    MARKET_CRASH = "market_crash"  # 市场崩盘
    POSITION_AT_RISK = "position_at_risk"  # 仓位风险过高
    SYSTEM_FAILURE = "system_failure"  # 系统故障
    EXECUTION_FAILURE = "execution_failure"  # 执行失败
    DATA_CORRUPTION = "data_corruption"  # 数据损坏


class EmergencyAction(Enum):
    """紧急处理动作"""
    CLOSE_ALL_POSITIONS = "close_all_positions"  # 平掉所有仓位
    REDUCE_POSITION = "reduce_position"  # 减仓
    CANCEL_ALL_ORDERS = "cancel_all_orders"  # 取消所有订单
    SWITCH_TO_BACKUP = "switch_to_backup"  # 切换到备用系统
    PAUSE_TRADING = "pause_trading"  # 暂停交易
    RESTART_SYSTEM = "restart_system"  # 重启系统
    NOTIFY_ADMIN = "notify_admin"  # 通知管理员


@dataclass
class EmergencyEvent:
    """紧急事件"""
    event_id: str
    emergency_type: EmergencyType
    severity: int  # 1-5，5为最严重
    description: str
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    resolved: bool = False
    actions_taken: List[EmergencyAction] = field(default_factory=list)


@dataclass
class EmergencyPlan:
    """紧急处理方案"""
    emergency_type: EmergencyType
    severity_threshold: int  # 触发此方案的严重程度阈值
    actions: List[EmergencyAction]
    priority: int  # 优先级，数字越小优先级越高
    description: str
    conditions: Dict[str, Any] = field(default_factory=dict)  # 额外条件


class EmergencyHandler:
    """紧急情况处理器

    功能：
    1. 紧急事件检测和分类
    2. 自动执行紧急处理方案
    3. 手动干预接口
    4. 处理结果跟踪和报告
    5. 系统恢复和重启
    """

    def __init__(
            self,
            order_manager: OrderManager,
            executor: OKXOrderExecutor,
            symbol: str = "ETH-USDT-SWAP"
    ):
        """初始化紧急处理器

        Args:
            order_manager: 订单管理器
            executor: OKX执行器
            symbol: 交易对
        """
        self.order_manager = order_manager
        self.executor = executor
        self.symbol = symbol

        # 紧急事件记录
        self.emergency_events: Dict[str, EmergencyEvent] = {}
        self.event_history: List[EmergencyEvent] = []

        # 紧急处理方案
        self.emergency_plans: List[EmergencyPlan] = self._create_default_plans()

        # 处理状态
        self.is_handling_emergency = False
        self.current_emergency: Optional[EmergencyEvent] = None
        self.last_emergency_time = 0.0

        # 回调函数
        self.on_emergency_callbacks: List[Callable] = []
        self.on_action_complete_callbacks: List[Callable] = []

        # 统计信息
        self.stats = {
            "total_emergencies": 0,
            "auto_resolved": 0,
            "manual_resolved": 0,
            "failed_resolutions": 0,
            "last_resolution_time": 0.0
        }

        # 系统状态
        self.trading_paused = False
        self.emergency_mode = False

        # 锁，防止并发处理
        self._lock = threading.Lock()

    def _create_default_plans(self) -> List[EmergencyPlan]:
        """创建默认紧急处理方案"""
        return [
            # 连接丢失 - 低严重度
            EmergencyPlan(
                emergency_type=EmergencyType.CONNECTION_LOST,
                severity_threshold=2,
                actions=[EmergencyAction.SWITCH_TO_BACKUP, EmergencyAction.PAUSE_TRADING],
                priority=3,
                description="连接丢失，切换到备用连接并暂停交易"
            ),

            # 连接丢失 - 高严重度
            EmergencyPlan(
                emergency_type=EmergencyType.CONNECTION_LOST,
                severity_threshold=4,
                actions=[EmergencyAction.CLOSE_ALL_POSITIONS, EmergencyAction.CANCEL_ALL_ORDERS,
                         EmergencyAction.RESTART_SYSTEM],
                priority=1,
                description="严重连接丢失，平仓并重启系统"
            ),

            # 市场崩盘
            EmergencyPlan(
                emergency_type=EmergencyType.MARKET_CRASH,
                severity_threshold=3,
                actions=[EmergencyAction.CLOSE_ALL_POSITIONS, EmergencyAction.CANCEL_ALL_ORDERS],
                priority=1,
                description="市场异常波动，立即平仓"
            ),

            # 仓位风险过高
            EmergencyPlan(
                emergency_type=EmergencyType.POSITION_AT_RISK,
                severity_threshold=3,
                actions=[EmergencyAction.REDUCE_POSITION, EmergencyAction.CLOSE_ALL_POSITIONS],
                priority=2,
                description="仓位风险过高，减仓或平仓"
            ),

            # 系统故障
            EmergencyPlan(
                emergency_type=EmergencyType.SYSTEM_FAILURE,
                severity_threshold=3,
                actions=[EmergencyAction.PAUSE_TRADING, EmergencyAction.NOTIFY_ADMIN, EmergencyAction.RESTART_SYSTEM],
                priority=1,
                description="系统故障，暂停交易并重启"
            ),

            # 执行失败
            EmergencyPlan(
                emergency_type=EmergencyType.EXECUTION_FAILURE,
                severity_threshold=3,
                actions=[EmergencyAction.CANCEL_ALL_ORDERS, EmergencyAction.PAUSE_TRADING],
                priority=2,
                description="订单执行失败，取消订单并暂停"
            ),
        ]

    async def handle_risk_alert(self, risk_alert: RiskAlert):
        """处理风险告警，可能触发紧急事件"""
        try:
            # 根据风险告警类型映射到紧急事件
            emergency_type = self._map_risk_alert_to_emergency(risk_alert)
            if not emergency_type:
                return

            # 根据风险级别确定严重程度
            severity = self._map_risk_level_to_severity(risk_alert.risk_level)

            # 创建紧急事件
            event_id = f"emergency_{risk_alert.alert_id}"
            description = f"由风险告警触发: {risk_alert.message}"

            event = EmergencyEvent(
                event_id=event_id,
                emergency_type=emergency_type,
                severity=severity,
                description=description,
                timestamp=time.time(),
                metadata={
                    "risk_alert": risk_alert.__dict__,
                    "component": risk_alert.component
                }
            )

            # 处理紧急事件
            await self.handle_emergency(event)

        except Exception as e:
            logger.error(f"处理风险告警失败: {e}")

    def _map_risk_alert_to_emergency(self, risk_alert: RiskAlert) -> Optional[EmergencyType]:
        """将风险告警映射到紧急事件类型"""
        component = risk_alert.component
        message = risk_alert.message.lower()

        if component == "system":
            if "connection" in message:
                return EmergencyType.CONNECTION_LOST
            elif "memory" in message or "cpu" in message:
                return EmergencyType.SYSTEM_FAILURE

        elif component == "market":
            if "波动率" in message or "volatility" in message:
                return EmergencyType.MARKET_CRASH
            elif "成交量" in message or "volume" in message:
                return EmergencyType.MARKET_CRASH

        elif component == "position":
            if "回撤" in message or "drawdown" in message:
                return EmergencyType.POSITION_AT_RISK
            elif "止损" in message or "stop loss" in message:
                return EmergencyType.POSITION_AT_RISK

        return None

    def _map_risk_level_to_severity(self, risk_level: RiskLevel) -> int:
        """将风险级别映射到严重程度"""
        mapping = {
            RiskLevel.NORMAL: 1,
            RiskLevel.WARNING: 2,
            RiskLevel.HIGH: 3,
            RiskLevel.CRITICAL: 5
        }
        return mapping.get(risk_level, 2)

    async def handle_emergency(self, emergency_event: EmergencyEvent):
        """处理紧急事件"""
        with self._lock:
            if self.is_handling_emergency:
                logger.warning(f"正在处理其他紧急事件，新事件排队: {emergency_event.event_id}")
                # 可以加入队列，这里简单记录
                return

            self.is_handling_emergency = True
            self.current_emergency = emergency_event

        try:
            logger.critical(f"🚨 处理紧急事件: {emergency_event.description}")
            logger.critical(f"    类型: {emergency_event.emergency_type.value}")
            logger.critical(f"    严重程度: {emergency_event.severity}/5")

            # 记录事件
            self.emergency_events[emergency_event.event_id] = emergency_event
            self.event_history.append(emergency_event)
            self.stats["total_emergencies"] += 1

            # 触发紧急通知
            await self._notify_emergency(emergency_event)

            # 查找合适的处理方案
            plan = self._find_emergency_plan(emergency_event)
            if not plan:
                logger.error(f"未找到适合的紧急处理方案: {emergency_event.emergency_type}")
                return

            logger.info(f"📋 执行紧急处理方案: {plan.description}")

            # 执行处理方案
            success = await self._execute_emergency_plan(plan, emergency_event)

            if success:
                emergency_event.resolved = True
                self.stats["auto_resolved"] += 1
                logger.info(f"✅ 紧急事件处理成功: {emergency_event.event_id}")
            else:
                self.stats["failed_resolutions"] += 1
                logger.error(f"❌ 紧急事件处理失败: {emergency_event.event_id}")

            # 更新状态
            self.last_emergency_time = time.time()
            self.stats["last_resolution_time"] = time.time()

            # 触发处理完成通知
            await self._notify_resolution(emergency_event, success)

        except Exception as e:
            logger.error(f"处理紧急事件时发生错误: {e}")
            self.stats["failed_resolutions"] += 1
        finally:
            with self._lock:
                self.is_handling_emergency = False
                self.current_emergency = None

    def _find_emergency_plan(self, event: EmergencyEvent) -> Optional[EmergencyPlan]:
        """查找适合的紧急处理方案"""
        suitable_plans = [
            plan for plan in self.emergency_plans
            if plan.emergency_type == event.emergency_type
               and plan.severity_threshold <= event.severity
        ]

        if not suitable_plans:
            return None

        # 按优先级排序，选择优先级最高的
        suitable_plans.sort(key=lambda p: p.priority)
        return suitable_plans[0]

    async def _execute_emergency_plan(
            self,
            plan: EmergencyPlan,
            event: EmergencyEvent
    ) -> bool:
        """执行紧急处理方案"""
        try:
            actions_success = []

            for action in plan.actions:
                logger.info(f"  执行动作: {action.value}")

                success = await self._execute_emergency_action(action, event)
                actions_success.append(success)

                if success:
                    event.actions_taken.append(action)
                else:
                    logger.warning(f"    动作执行失败: {action.value}")

                # 触发动作完成回调
                await self._notify_action_complete(action, success, event)

                # 动作间短暂延迟
                await asyncio.sleep(0.5)

            # 所有动作都成功才算成功
            return all(actions_success)

        except Exception as e:
            logger.error(f"执行紧急处理方案失败: {e}")
            return False

    async def _execute_emergency_action(
            self,
            action: EmergencyAction,
            event: EmergencyEvent
    ) -> bool:
        """执行单个紧急动作"""
        try:
            if action == EmergencyAction.CLOSE_ALL_POSITIONS:
                return await self._close_all_positions()

            elif action == EmergencyAction.REDUCE_POSITION:
                return await self._reduce_position()

            elif action == EmergencyAction.CANCEL_ALL_ORDERS:
                return await self._cancel_all_orders()

            elif action == EmergencyAction.SWITCH_TO_BACKUP:
                return await self._switch_to_backup()

            elif action == EmergencyAction.PAUSE_TRADING:
                return self._pause_trading()

            elif action == EmergencyAction.RESTART_SYSTEM:
                return await self._restart_system()

            elif action == EmergencyAction.NOTIFY_ADMIN:
                return self._notify_admin(event)

            else:
                logger.warning(f"未知的紧急动作: {action}")
                return False

        except Exception as e:
            logger.error(f"执行紧急动作失败 {action.value}: {e}")
            return False

    async def _close_all_positions(self) -> bool:
        """平掉所有仓位"""
        try:
            logger.info("📉 执行紧急平仓")

            # 这里应该获取当前仓位并平仓
            # 暂时返回成功
            return True

        except Exception as e:
            logger.error(f"紧急平仓失败: {e}")
            return False

    async def _reduce_position(self) -> bool:
        """减仓"""
        try:
            logger.info("📉 执行紧急减仓")

            # 这里应该获取当前仓位并减仓
            # 暂时返回成功
            return True

        except Exception as e:
            logger.error(f"紧急减仓失败: {e}")
            return False

    async def _cancel_all_orders(self) -> bool:
        """取消所有订单"""
        try:
            logger.info("❌ 取消所有订单")

            # 从订单管理器获取活跃订单并取消
            if self.order_manager:
                active_orders = await self.order_manager.get_active_orders(self.symbol)
                for order in active_orders:
                    try:
                        await self.order_manager.cancel_order(order.order_id, "紧急取消")
                    except Exception as e:
                        logger.error(f"取消订单失败 {order.order_id}: {e}")

            return True

        except Exception as e:
            logger.error(f"取消所有订单失败: {e}")
            return False

    async def _switch_to_backup(self) -> bool:
        """切换到备用系统"""
        try:
            logger.info("🔄 切换到备用系统")

            # 这里应该实现切换到备用连接或系统的逻辑
            # 暂时返回成功
            return True

        except Exception as e:
            logger.error(f"切换到备用系统失败: {e}")
            return False

    def _pause_trading(self) -> bool:
        """暂停交易"""
        try:
            logger.info("⏸️ 暂停交易")
            self.trading_paused = True
            return True

        except Exception as e:
            logger.error(f"暂停交易失败: {e}")
            return False

    async def _restart_system(self) -> bool:
        """重启系统"""
        try:
            logger.info("🔄 重启系统")

            # 这里应该实现系统重启逻辑
            # 暂时返回成功
            return True

        except Exception as e:
            logger.error(f"重启系统失败: {e}")
            return False

    def _notify_admin(self, event: EmergencyEvent) -> bool:
        """通知管理员"""
        try:
            logger.info(f"📧 通知管理员: {event.description}")

            # 这里应该实现通知逻辑（邮件、短信、API等）
            # 暂时返回成功
            return True

        except Exception as e:
            logger.error(f"通知管理员失败: {e}")
            return False

    async def _notify_emergency(self, event: EmergencyEvent):
        """通知紧急事件"""
        for callback in self.on_emergency_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"紧急事件回调执行失败: {e}")

    async def _notify_resolution(self, event: EmergencyEvent, success: bool):
        """通知处理结果"""
        # 可以添加专门的处理结果回调
        pass

    async def _notify_action_complete(self, action: EmergencyAction, success: bool, event: EmergencyEvent):
        """通知动作完成"""
        for callback in self.on_action_complete_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(action, success, event)
                else:
                    callback(action, success, event)
            except Exception as e:
                logger.error(f"动作完成回调执行失败: {e}")

    def register_emergency_callback(self, callback: Callable):
        """注册紧急事件回调"""
        self.on_emergency_callbacks.append(callback)

    def register_action_complete_callback(self, callback: Callable):
        """注册动作完成回调"""
        self.on_action_complete_callbacks.append(callback)

    def add_emergency_plan(self, plan: EmergencyPlan):
        """添加紧急处理方案"""
        self.emergency_plans.append(plan)
        # 按优先级排序
        self.emergency_plans.sort(key=lambda p: p.priority)

    def remove_emergency_plan(self, plan_id: str):
        """移除紧急处理方案"""
        # 这里需要扩展EmergencyPlan以包含id
        pass

    def get_active_emergencies(self) -> List[EmergencyEvent]:
        """获取活跃的紧急事件"""
        return [
            event for event in self.emergency_events.values()
            if not event.resolved
        ]

    def get_emergency_history(self, limit: int = 100) -> List[EmergencyEvent]:
        """获取紧急事件历史"""
        return self.event_history[-limit:]

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            **self.stats,
            "trading_paused": self.trading_paused,
            "emergency_mode": self.emergency_mode,
            "active_emergencies": len(self.get_active_emergencies()),
            "total_plans": len(self.emergency_plans),
            "last_emergency_time": self.last_emergency_time
        }

    def is_trading_paused(self) -> bool:
        """检查交易是否暂停"""
        return self.trading_paused

    def resume_trading(self):
        """恢复交易"""
        self.trading_paused = False
        logger.info("▶️ 交易已恢复")

    def clear_resolved_events(self):
        """清理已解决的紧急事件"""
        to_remove = [
            event_id for event_id, event in self.emergency_events.items()
            if event.resolved and time.time() - event.timestamp > 3600  # 1小时前已解决
        ]

        for event_id in to_remove:
            del self.emergency_events[event_id]

        logger.info(f"🧹 清理了 {len(to_remove)} 个已解决的紧急事件")


async def test_emergency_handler():
    """测试紧急情况处理器"""
    print("🧪 测试紧急情况处理器")
    print("=" * 60)

    from src.strategy.triplea.execution.okx_executor import OKXAPIConfig, OKXOrderExecutor
    from src.strategy.triplea.risk.real_time_risk_monitor import RiskAlert, RiskLevel

    # 创建配置
    config = OKXAPIConfig(use_simulation=True)

    async with OKXOrderExecutor(config) as executor:
        # 创建模拟的订单管理器
        class MockOrderManager:
            async def get_active_orders(self, symbol=None):
                return []

            async def cancel_order(self, order_id, reason):
                print(f"  模拟取消订单: {order_id} ({reason})")
                return True

        order_manager = MockOrderManager()

        # 创建紧急处理器
        emergency_handler = EmergencyHandler(order_manager, executor)

        # 注册回调
        def on_emergency_callback(event):
            print(f"🔔 收到紧急事件: {event.description}")

        def on_action_complete_callback(action, success, event):
            print(f"  动作完成: {action.value} ({'成功' if success else '失败'})")

        emergency_handler.register_emergency_callback(on_emergency_callback)
        emergency_handler.register_action_complete_callback(on_action_complete_callback)

        # 测试1：创建紧急事件
        print("\n📊 测试1：创建连接丢失紧急事件")
        event = EmergencyEvent(
            event_id="test_connection_lost_001",
            emergency_type=EmergencyType.CONNECTION_LOST,
            severity=4,
            description="WebSocket连接丢失超过30秒",
            timestamp=time.time(),
            metadata={"connection_type": "websocket", "duration_seconds": 35}
        )

        await emergency_handler.handle_emergency(event)

        # 测试2：创建风险告警
        print("\n📊 测试2：处理风险告警")
        risk_alert = RiskAlert(
            alert_id="test_risk_alert_001",
            component="system",
            risk_level=RiskLevel.CRITICAL,
            message="API连接超时",
            timestamp=time.time(),
            metadata={"timeout_seconds": 15}
        )

        await emergency_handler.handle_risk_alert(risk_alert)

        # 测试3：获取统计信息
        print("\n📊 测试3：获取统计信息")
        stats = emergency_handler.get_statistics()
        for key, value in stats.items():
            print(f"  {key}: {value}")

        # 测试4：获取紧急事件历史
        print("\n📊 测试4：紧急事件历史")
        history = emergency_handler.get_emergency_history()
        for event in history:
            print(f"  • {event.event_id}: {event.description} (解决: {event.resolved})")

        # 测试5：清理已解决事件
        print("\n📊 测试5：清理已解决事件")
        emergency_handler.clear_resolved_events()

    print("\n✅ 紧急情况处理器测试完成")


if __name__ == "__main__":
    asyncio.run(test_emergency_handler())
