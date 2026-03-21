#!/usr/bin/env python3
"""
订单状态管理器
管理四号引擎的订单生命周期，提供订单跟踪、状态同步和错误恢复功能
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Set, Callable

from src.strategy.triplea.okx_executor import (
    OKXOrderExecutor, OrderStatus, OrderSide
)
from src.utils.log import get_logger

logger = get_logger(__name__)


class OrderLifecycle(Enum):
    """订单生命周期阶段"""
    CREATED = "created"  # 已创建
    SUBMITTED = "submitted"  # 已提交到交易所
    PENDING = "pending"  # 等待成交
    PARTIALLY_FILLED = "partially_filled"  # 部分成交
    FILLED = "filled"  # 完全成交
    CANCELLING = "cancelling"  # 取消中
    CANCELLED = "cancelled"  # 已取消
    REJECTED = "rejected"  # 被拒绝
    EXPIRED = "expired"  # 已过期
    ERROR = "error"  # 错误状态


class OrderErrorType(Enum):
    """订单错误类型"""
    NETWORK_ERROR = "network_error"  # 网络错误
    API_ERROR = "api_error"  # API错误
    INSUFFICIENT_BALANCE = "insufficient_balance"  # 余额不足
    POSITION_LIMIT = "position_limit"  # 持仓限制
    RATE_LIMIT = "rate_limit"  # 频率限制
    MARKET_CLOSED = "market_closed"  # 市场关闭
    INVALID_PARAMETER = "invalid_parameter"  # 参数无效
    UNKNOWN_ERROR = "unknown_error"  # 未知错误


@dataclass
class ManagedOrder:
    """托管订单数据类"""
    # 基本订单信息
    order_id: str  # 订单ID
    client_oid: str  # 客户端订单ID
    symbol: str  # 交易对
    side: OrderSide  # 买卖方向
    order_type: str  # 订单类型
    size: float  # 委托数量
    price: Optional[float]  # 委托价格

    # 状态信息
    lifecycle: OrderLifecycle  # 生命周期阶段
    current_status: OrderStatus  # 当前订单状态
    filled_size: float  # 已成交数量
    avg_fill_price: float  # 平均成交价格
    fee: float  # 手续费

    # 时间戳
    created_time: float  # 创建时间
    submitted_time: Optional[float]  # 提交时间
    filled_time: Optional[float]  # 完全成交时间
    cancelled_time: Optional[float]  # 取消时间
    last_update_time: float  # 最后更新时间

    # 错误信息
    error_type: Optional[OrderErrorType] = None
    error_message: Optional[str] = None
    retry_count: int = 0  # 重试次数

    # 附加信息
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据
    tags: Set[str] = field(default_factory=set)  # 标签


@dataclass
class OrderEvent:
    """订单事件数据类"""
    event_type: str  # 事件类型
    order_id: str  # 订单ID
    timestamp: float  # 时间戳
    data: Dict[str, Any]  # 事件数据
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据


class OrderCallback:
    """订单回调函数封装"""

    def __init__(self, callback: Callable, filter_tags: Set[str] = None):
        self.callback = callback
        self.filter_tags = filter_tags or set()

    async def execute(self, order: ManagedOrder, event: OrderEvent):
        """执行回调"""
        try:
            # 检查标签过滤
            if self.filter_tags and not self.filter_tags.intersection(order.tags):
                return

            if asyncio.iscoroutinefunction(self.callback):
                await self.callback(order, event)
            else:
                self.callback(order, event)
        except Exception as e:
            logger.error(f"订单回调执行失败: {e}")


class OrderManager:
    """订单状态管理器"""

    def __init__(self, executor: OKXOrderExecutor, sync_interval: float = 5.0):
        self.executor = executor
        self.sync_interval = sync_interval

        # 订单存储
        self.orders: Dict[str, ManagedOrder] = {}
        self.order_by_client_oid: Dict[str, str] = {}  # client_oid -> order_id

        # 事件系统
        self.event_queue = asyncio.Queue()
        self.callbacks: Dict[str, List[OrderCallback]] = defaultdict(list)

        # 同步状态
        self.sync_task: Optional[asyncio.Task] = None
        self.running = False
        self.last_sync_time = 0.0

        # 性能统计
        self.stats = {
            "total_orders": 0,
            "active_orders": 0,
            "filled_orders": 0,
            "cancelled_orders": 0,
            "rejected_orders": 0,
            "avg_fill_time_ms": 0.0,
            "total_fill_time_ms": 0.0
        }

        # 错误处理
        self.max_retries = 3
        self.retry_delay = 1.0

    async def start(self):
        """启动订单管理器"""
        if self.running:
            return

        self.running = True
        self.sync_task = asyncio.create_task(self._sync_loop())
        self.event_task = asyncio.create_task(self._process_events())

        logger.info("订单管理器已启动")

    async def stop(self):
        """停止订单管理器"""
        if not self.running:
            return

        self.running = False

        if self.sync_task:
            self.sync_task.cancel()
            try:
                await self.sync_task
            except asyncio.CancelledError:
                pass

        if self.event_task:
            self.event_task.cancel()
            try:
                await self.event_task
            except asyncio.CancelledError:
                pass

        logger.info("订单管理器已停止")

    async def create_order(self, symbol: str, side: OrderSide, order_type: str,
                           size: float, price: Optional[float] = None,
                           tags: Set[str] = None, metadata: Dict = None) -> Optional[ManagedOrder]:
        """创建托管订单"""
        # 生成客户端订单ID
        client_oid = self._generate_client_oid(symbol, side)

        # 创建托管订单
        order = ManagedOrder(
            order_id="",  # 将由交易所分配
            client_oid=client_oid,
            symbol=symbol,
            side=side,
            order_type=order_type,
            size=size,
            price=price,
            lifecycle=OrderLifecycle.CREATED,
            current_status=OrderStatus.LIVE,
            filled_size=0.0,
            avg_fill_price=0.0,
            fee=0.0,
            created_time=time.time(),
            submitted_time=None,
            filled_time=None,
            cancelled_time=None,
            last_update_time=time.time(),
            tags=tags or set(),
            metadata=metadata or {}
        )

        # 临时存储（等待提交）
        temp_order_id = f"temp_{client_oid}"
        self.orders[temp_order_id] = order
        self.order_by_client_oid[client_oid] = temp_order_id

        # 发送创建事件
        await self._emit_event("order_created", order, {
            "symbol": symbol,
            "side": side.value,
            "order_type": order_type,
            "size": size,
            "price": price
        })

        # 提交订单到交易所
        success = await self._submit_order(order)
        if not success:
            return None

        return order

    async def _submit_order(self, order: ManagedOrder) -> bool:
        """提交订单到交易所"""
        try:
            # 调用执行器下单
            from src.strategy.triplea.okx_executor import OrderRequest, OrderType

            # 映射订单类型
            order_type_map = {
                "market": OrderType.MARKET,
                "limit": OrderType.LIMIT,
                "ioc": OrderType.IOC,
                "fok": OrderType.FOK
            }

            order_type = order_type_map.get(order.order_type.lower(), OrderType.MARKET)

            order_request = OrderRequest(
                symbol=order.symbol,
                side=order.side,
                order_type=order_type,
                size=order.size,
                price=order.price,
                client_oid=order.client_oid
            )

            order_response = await self.executor.place_order(order_request)

            if not order_response:
                # 下单失败
                order.lifecycle = OrderLifecycle.ERROR
                order.error_type = OrderErrorType.API_ERROR
                order.error_message = "下单失败"
                order.last_update_time = time.time()

                await self._emit_event("order_submit_failed", order, {
                    "error": "下单失败"
                })

                return False

            # 更新订单信息
            order.order_id = order_response.order_id
            order.submitted_time = time.time()
            order.lifecycle = OrderLifecycle.SUBMITTED
            order.current_status = order_response.status
            order.last_update_time = time.time()

            # 更新存储映射
            self.orders[order.order_id] = order
            self.order_by_client_oid[order.client_oid] = order.order_id

            # 删除临时存储
            temp_order_id = f"temp_{order.client_oid}"
            if temp_order_id in self.orders:
                del self.orders[temp_order_id]

            # 发送提交成功事件
            await self._emit_event("order_submitted", order, {
                "exchange_order_id": order.order_id
            })

            # 更新统计
            self.stats["total_orders"] += 1
            self.stats["active_orders"] += 1

            return True

        except Exception as e:
            logger.error(f"提交订单失败: {e}")
            order.lifecycle = OrderLifecycle.ERROR
            order.error_type = OrderErrorType.UNKNOWN_ERROR
            order.error_message = str(e)
            order.last_update_time = time.time()

            await self._emit_event("order_submit_error", order, {
                "error": str(e)
            })

            return False

    async def cancel_order(self, order_id: str, reason: str = None) -> bool:
        """取消订单"""
        if order_id not in self.orders:
            logger.error(f"订单不存在: {order_id}")
            return False

        order = self.orders[order_id]

        # 检查是否可以取消
        if order.lifecycle in [OrderLifecycle.FILLED, OrderLifecycle.CANCELLED,
                               OrderLifecycle.REJECTED, OrderLifecycle.EXPIRED]:
            logger.warning(f"订单状态无法取消: {order.lifecycle}")
            return False

        # 更新生命周期
        order.lifecycle = OrderLifecycle.CANCELLING
        order.last_update_time = time.time()

        await self._emit_event("order_cancelling", order, {
            "reason": reason
        })

        # 调用执行器取消订单
        success = await self.executor.cancel_order(order_id, order.symbol)

        if success:
            order.lifecycle = OrderLifecycle.CANCELLED
            order.current_status = OrderStatus.CANCELLED
            order.cancelled_time = time.time()
            order.last_update_time = time.time()

            # 更新统计
            self.stats["active_orders"] -= 1
            self.stats["cancelled_orders"] += 1

            await self._emit_event("order_cancelled", order, {
                "reason": reason
            })
        else:
            order.lifecycle = OrderLifecycle.ERROR
            order.error_type = OrderErrorType.API_ERROR
            order.error_message = "取消订单失败"
            order.last_update_time = time.time()

            await self._emit_event("order_cancel_failed", order, {
                "reason": reason
            })

        return success

    async def get_order(self, order_id: str = None, client_oid: str = None) -> Optional[ManagedOrder]:
        """获取订单"""
        if order_id:
            return self.orders.get(order_id)
        elif client_oid:
            actual_order_id = self.order_by_client_oid.get(client_oid)
            return self.orders.get(actual_order_id) if actual_order_id else None
        return None

    async def get_orders_by_filter(self, filter_func: Callable[[ManagedOrder], bool]) -> List[ManagedOrder]:
        """根据过滤器获取订单"""
        return [order for order in self.orders.values() if filter_func(order)]

    async def get_active_orders(self, symbol: str = None) -> List[ManagedOrder]:
        """获取活跃订单"""

        def is_active(order: ManagedOrder) -> bool:
            if order.lifecycle in [OrderLifecycle.FILLED, OrderLifecycle.CANCELLED,
                                   OrderLifecycle.REJECTED, OrderLifecycle.EXPIRED,
                                   OrderLifecycle.ERROR]:
                return False
            if symbol and order.symbol != symbol:
                return False
            return True

        return await self.get_orders_by_filter(is_active)

    async def _sync_loop(self):
        """订单同步循环"""
        while self.running:
            try:
                await self._sync_orders()
                await asyncio.sleep(self.sync_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"订单同步错误: {e}")
                await asyncio.sleep(self.sync_interval * 2)  # 错误时延长等待

    async def _sync_orders(self):
        """同步订单状态"""
        try:
            # 获取所有活跃订单
            active_orders = await self.get_active_orders()

            for order in active_orders:
                if order.lifecycle == OrderLifecycle.CREATED:
                    continue  # 未提交的订单不同步

                # 从交易所获取最新状态
                order_response = await self.executor.get_order_status(
                    order.order_id, order.symbol
                )

                if not order_response:
                    continue

                # 检查状态变化
                old_status = order.current_status
                new_status = order_response.status

                if old_status != new_status:
                    # 状态发生变化
                    order.current_status = new_status
                    order.filled_size = order_response.filled_size
                    order.avg_fill_price = order_response.avg_fill_price
                    order.fee = order_response.fee
                    order.last_update_time = time.time()

                    # 更新生命周期
                    if new_status == OrderStatus.FILLED:
                        order.lifecycle = OrderLifecycle.FILLED
                        order.filled_time = time.time()

                        # 计算成交时间
                        if order.submitted_time:
                            fill_time_ms = (order.filled_time - order.submitted_time) * 1000
                            self.stats["total_fill_time_ms"] += fill_time_ms
                            self.stats["filled_orders"] += 1
                            self.stats["active_orders"] -= 1
                            if self.stats["filled_orders"] > 0:
                                self.stats["avg_fill_time_ms"] = (
                                        self.stats["total_fill_time_ms"] / self.stats["filled_orders"]
                                )

                    elif new_status == OrderStatus.CANCELLED:
                        order.lifecycle = OrderLifecycle.CANCELLED
                        order.cancelled_time = time.time()
                        self.stats["active_orders"] -= 1
                        self.stats["cancelled_orders"] += 1

                    elif new_status == OrderStatus.REJECTED:
                        order.lifecycle = OrderLifecycle.REJECTED
                        self.stats["active_orders"] -= 1
                        self.stats["rejected_orders"] += 1

                    # 发送状态变化事件
                    await self._emit_event("order_status_changed", order, {
                        "old_status": old_status.value,
                        "new_status": new_status.value,
                        "filled_size": order.filled_size,
                        "avg_price": order.avg_fill_price
                    })

            self.last_sync_time = time.time()

        except Exception as e:
            logger.error(f"同步订单状态失败: {e}")

    async def _process_events(self):
        """处理事件队列"""
        while self.running:
            try:
                event = await self.event_queue.get()

                # 触发相关回调
                callbacks = self.callbacks.get(event.event_type, [])
                if not callbacks:
                    continue

                # 获取订单
                order = await self.get_order(event.order_id)
                if not order:
                    continue

                # 执行回调
                for callback in callbacks:
                    await callback.execute(order, event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"处理事件失败: {e}")

    async def _emit_event(self, event_type: str, order: ManagedOrder, data: Dict):
        """发送事件"""
        event = OrderEvent(
            event_type=event_type,
            order_id=order.order_id or f"temp_{order.client_oid}",
            timestamp=time.time(),
            data=data,
            metadata={"client_oid": order.client_oid}
        )

        await self.event_queue.put(event)

    def register_callback(self, event_type: str, callback: Callable, filter_tags: Set[str] = None):
        """注册事件回调"""
        callback_obj = OrderCallback(callback, filter_tags)
        self.callbacks[event_type].append(callback_obj)

    def _generate_client_oid(self, symbol: str, side: OrderSide) -> str:
        """生成客户端订单ID"""
        timestamp = int(time.time() * 1000)
        random_part = hash(f"{symbol}{side}{timestamp}") % 10000
        return f"triplea_{symbol}_{side.value}_{timestamp}_{random_part:04d}"

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            "last_sync_time": self.last_sync_time,
            "total_managed_orders": len(self.orders),
            "unique_symbols": len(set(order.symbol for order in self.orders.values()))
        }


async def test_order_manager():
    """测试订单管理器"""
    print("🧪 测试订单管理器...")

    from src.strategy.triplea.okx_executor import OKXAPIConfig, OKXOrderExecutor

    # 创建配置
    config = OKXAPIConfig(use_simulation=True)

    async with OKXOrderExecutor(config) as executor:
        # 创建订单管理器
        manager = OrderManager(executor, sync_interval=2.0)

        # 注册回调
        def on_order_created(order, event):
            print(f"📝 订单创建: {order.client_oid}")

        def on_order_filled(order, event):
            print(f"✅ 订单成交: {order.order_id}, 数量: {order.filled_size}")

        manager.register_callback("order_created", on_order_created)
        manager.register_callback("order_status_changed", on_order_filled)

        # 启动管理器
        await manager.start()

        # 创建测试订单
        print("1. 创建测试订单...")
        test_order = await manager.create_order(
            symbol="ETH-USDT-SWAP",
            side=OrderSide.BUY,
            order_type="market",
            size=0.01,
            tags={"test", "market_order"}
        )

        if test_order:
            print(f"   订单创建成功: {test_order.client_oid}")
            print(f"   生命周期: {test_order.lifecycle}")
        else:
            print("   订单创建失败")

        # 等待一段时间
        print("2. 等待订单处理...")
        await asyncio.sleep(5)

        # 获取统计信息
        print("3. 获取统计信息...")
        stats = manager.get_statistics()
        print(f"   统计信息: {stats}")

        # 获取活跃订单
        print("4. 获取活跃订单...")
        active_orders = await manager.get_active_orders()
        print(f"   活跃订单数: {len(active_orders)}")

        # 停止管理器
        print("5. 停止订单管理器...")
        await manager.stop()

    print("✅ 订单管理器测试完成")


if __name__ == "__main__":
    asyncio.run(test_order_manager())
