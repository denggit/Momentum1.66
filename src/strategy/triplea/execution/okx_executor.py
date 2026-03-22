#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四号引擎OKX API执行器（增强版）
专为科考船实盘测试设计，提供稳定可靠的订单执行功能
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple

import aiohttp

from src.utils.log import get_logger

logger = get_logger(__name__)


class OrderType(Enum):
    """订单类型枚举"""
    MARKET = "market"  # 市价单
    LIMIT = "limit"  # 限价单
    POST_ONLY = "post_only"  # 只做maker单
    FOK = "fok"  # 全部成交或取消
    IOC = "ioc"  # 立即成交或取消


class OrderSide(Enum):
    """订单方向枚举"""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    """订单状态枚举"""
    LIVE = "live"  # 等待成交
    PARTIALLY_FILLED = "partially_filled"  # 部分成交
    FILLED = "filled"  # 完全成交
    CANCELLED = "canceled"  # 已取消
    REJECTED = "rejected"  # 被拒绝
    EXPIRED = "expired"  # 已过期


@dataclass
class OrderRequest:
    """订单请求数据类"""
    symbol: str  # 交易对，如 "ETH-USDT-SWAP"
    side: OrderSide  # 买卖方向
    order_type: OrderType  # 订单类型
    size: float  # 委托数量
    price: Optional[float] = None  # 委托价格（限价单需要）
    client_oid: Optional[str] = None  # 客户端订单ID
    reduce_only: bool = False  # 是否只减仓
    time_in_force: str = "normal"  # 订单有效时间策略


@dataclass
class OrderResponse:
    """订单响应数据类"""
    order_id: str  # 订单ID
    client_oid: Optional[str]  # 客户端订单ID
    symbol: str  # 交易对
    side: OrderSide  # 买卖方向
    order_type: OrderType  # 订单类型
    size: float  # 委托数量
    price: Optional[float]  # 委托价格
    status: OrderStatus  # 订单状态
    filled_size: float  # 已成交数量
    avg_fill_price: float  # 平均成交价格
    fee: float  # 手续费
    created_time: float  # 创建时间戳
    update_time: float  # 更新时间戳


@dataclass
class ExecutionReport:
    """执行报告数据类"""
    order_id: str  # 订单ID
    client_oid: Optional[str]  # 客户端订单ID
    symbol: str  # 交易对
    side: OrderSide  # 买卖方向
    executed_size: float  # 已执行数量
    executed_price: float  # 执行价格
    remaining_size: float  # 剩余数量
    fee: float  # 手续费
    fee_currency: str  # 手续费币种
    trade_time: float  # 成交时间戳
    latency_ms: float  # 执行延迟（毫秒）


class OKXAPIConfig:
    """OKX API配置类"""

    def __init__(self, api_key: str = "", api_secret: str = "", passphrase: str = "",
                 use_simulation: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.use_simulation = use_simulation

        # API端点
        if use_simulation:
            self.base_url = "https://www.okx.com"
            self.ws_public_url = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"
            self.ws_private_url = "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"
        else:
            self.base_url = "https://www.okx.com"
            self.ws_public_url = "wss://ws.okx.com:8443/ws/v5/public"
            self.ws_private_url = "wss://ws.okx.com:8443/ws/v5/private"

        # API路径
        self.endpoints = {
            "place_order": "/api/v5/trade/order",
            "cancel_order": "/api/v5/trade/cancel-order",
            "amend_order": "/api/v5/trade/amend-order",
            "get_order": "/api/v5/trade/order",
            "get_orders_pending": "/api/v5/trade/orders-pending",
            "get_orders_history": "/api/v5/trade/orders-history",
            "get_fills": "/api/v5/trade/fills",
            "get_account_balance": "/api/v5/account/balance",
            "get_positions": "/api/v5/account/positions",
            "get_instruments": "/api/v5/public/instruments"
        }

        # 请求配置
        self.timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=30)
        self.retry_count = 3
        self.retry_delay = 1.0


class ConnectionHealthMonitor:
    """连接健康监控器"""

    def __init__(self):
        self.connection_stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_latency_ms": 0.0,
            "last_success_time": 0.0,
            "last_failure_time": 0.0,
            "consecutive_failures": 0
        }

        self.health_status = {
            "overall": "healthy",  # healthy, degraded, unhealthy
            "api_connectivity": "healthy",
            "order_execution": "healthy",
            "websocket": "healthy",
            "last_check": 0.0
        }

    def record_request(self, success: bool, latency_ms: float):
        """记录请求结果"""
        self.connection_stats["total_requests"] += 1
        self.connection_stats["total_latency_ms"] += latency_ms

        if success:
            self.connection_stats["successful_requests"] += 1
            self.connection_stats["last_success_time"] = time.time()
            self.connection_stats["consecutive_failures"] = 0
        else:
            self.connection_stats["failed_requests"] += 1
            self.connection_stats["last_failure_time"] = time.time()
            self.connection_stats["consecutive_failures"] += 1

        # 更新健康状态
        self._update_health_status()

    def _update_health_status(self):
        """更新健康状态"""
        total = self.connection_stats["total_requests"]
        if total == 0:
            return

        success_rate = self.connection_stats["successful_requests"] / total
        consecutive_failures = self.connection_stats["consecutive_failures"]

        if success_rate > 0.95 and consecutive_failures == 0:
            self.health_status["overall"] = "healthy"
        elif success_rate > 0.8 and consecutive_failures < 3:
            self.health_status["overall"] = "degraded"
        else:
            self.health_status["overall"] = "unhealthy"

        self.health_status["last_check"] = time.time()

    def get_stats(self) -> Dict:
        """获取统计信息"""
        total = self.connection_stats["total_requests"]
        avg_latency = (self.connection_stats["total_latency_ms"] / total
                       if total > 0 else 0)

        return {
            "total_requests": total,
            "success_rate": (self.connection_stats["successful_requests"] / total
                             if total > 0 else 1.0),
            "avg_latency_ms": avg_latency,
            "consecutive_failures": self.connection_stats["consecutive_failures"],
            "health_status": self.health_status["overall"],
            "last_success": self.connection_stats["last_success_time"],
            "last_failure": self.connection_stats["last_failure_time"]
        }

    def is_healthy(self) -> bool:
        """检查是否健康"""
        return self.health_status["overall"] in ["healthy", "degraded"]


class OKXOrderExecutor:
    """OKX订单执行器"""

    def __init__(self, config: OKXAPIConfig):
        self.config = config
        self.health_monitor = ConnectionHealthMonitor()
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_order_id = 0

        # 订单跟踪
        self.pending_orders: Dict[str, OrderResponse] = {}
        self.executed_orders: Dict[str, List[ExecutionReport]] = {}

        # 性能统计
        self.stats = {
            "total_orders": 0,
            "successful_orders": 0,
            "failed_orders": 0,
            "total_execution_latency_ms": 0.0,
            "last_order_time": 0.0
        }

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.disconnect()

    async def connect(self):
        """连接API"""
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.config.timeout,
                headers=self._get_default_headers()
            )
            logger.info("OKX API执行器已连接")

    async def disconnect(self):
        """断开连接"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("OKX API执行器已断开连接")

    def _get_default_headers(self) -> Dict:
        """获取默认请求头"""
        return {
            "User-Agent": "TripleA-Engine/3.0",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _generate_signature(self, timestamp: str, method: str, request_path: str,
                            body: str = "") -> str:
        """生成API签名"""
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            bytes(self.config.api_secret, encoding='utf-8'),
            bytes(message, encoding='utf-8'),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()

    def _get_auth_headers(self, method: str, request_path: str,
                          body: str = "") -> Dict:
        """获取认证头"""
        timestamp = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        signature = self._generate_signature(timestamp, method, request_path, body)

        return {
            "OK-ACCESS-KEY": self.config.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.config.passphrase
        }

    async def _make_request(self, method: str, endpoint: str,
                            params: Dict = None, data: Dict = None,
                            auth: bool = False) -> Tuple[bool, Any]:
        """发送HTTP请求"""
        if self.session is None:
            await self.connect()

        url = self.config.base_url + endpoint
        headers = self._get_default_headers()

        if auth:
            body = json.dumps(data) if data else ""
            headers.update(self._get_auth_headers(method, endpoint, body))

        start_time = time.perf_counter()
        success = False
        response_data = None

        for attempt in range(self.config.retry_count):
            try:
                async with self.session.request(
                        method=method,
                        url=url,
                        params=params,
                        json=data,
                        headers=headers
                ) as response:
                    latency_ms = (time.perf_counter() - start_time) * 1000

                    if response.status == 200:
                        result = await response.json()
                        if result.get("code") == "0":
                            success = True
                            response_data = result.get("data", [])
                            logger.debug(f"API请求成功: {endpoint} (延迟: {latency_ms:.1f}ms)")
                        else:
                            logger.error(f"API业务错误: {result.get('msg', 'Unknown error')}")
                            response_data = result
                    else:
                        logger.error(f"HTTP错误: {response.status} - {await response.text()}")

                    # 记录健康状态
                    self.health_monitor.record_request(success, latency_ms)

                    if success:
                        return True, response_data
                    else:
                        # 失败重试
                        if attempt < self.config.retry_count - 1:
                            await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                            continue

            except aiohttp.ClientError as e:
                latency_ms = (time.perf_counter() - start_time) * 1000
                logger.error(f"客户端错误: {e}")
                self.health_monitor.record_request(False, latency_ms)

                if attempt < self.config.retry_count - 1:
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                    continue

            except Exception as e:
                latency_ms = (time.perf_counter() - start_time) * 1000
                logger.error(f"未知错误: {e}")
                self.health_monitor.record_request(False, latency_ms)
                break

        return False, response_data

    async def place_order(self, order_request: OrderRequest) -> Optional[OrderResponse]:
        """下单"""
        start_time = time.perf_counter()

        # 生成客户端订单ID
        if order_request.client_oid is None:
            self.last_order_id += 1
            order_request.client_oid = f"triplea_{int(time.time())}_{self.last_order_id}"

        # 构建请求数据
        request_data = {
            "instId": order_request.symbol,
            "tdMode": "cross",  # 全仓模式
            "side": order_request.side.value,
            "ordType": order_request.order_type.value,
            "sz": str(order_request.size),
            "clOrdId": order_request.client_oid,
            "reduceOnly": order_request.reduce_only
        }

        if order_request.price is not None:
            request_data["px"] = str(order_request.price)

        # 发送下单请求
        success, response = await self._make_request(
            method="POST",
            endpoint=self.config.endpoints["place_order"],
            data=request_data,
            auth=True
        )

        if not success or not response:
            logger.error(f"下单失败: {order_request}")
            self.stats["failed_orders"] += 1
            return None

        # 解析响应
        order_data = response[0] if isinstance(response, list) else response
        order_id = order_data.get("ordId")

        if not order_id:
            logger.error(f"下单响应无效: {order_data}")
            self.stats["failed_orders"] += 1
            return None

        # 创建订单响应
        order_response = OrderResponse(
            order_id=order_id,
            client_oid=order_request.client_oid,
            symbol=order_request.symbol,
            side=order_request.side,
            order_type=order_request.order_type,
            size=order_request.size,
            price=order_request.price,
            status=OrderStatus.LIVE,
            filled_size=0.0,
            avg_fill_price=0.0,
            fee=0.0,
            created_time=time.time(),
            update_time=time.time()
        )

        # 更新统计
        self.stats["total_orders"] += 1
        self.stats["successful_orders"] += 1
        latency_ms = (time.perf_counter() - start_time) * 1000
        self.stats["total_execution_latency_ms"] += latency_ms
        self.stats["last_order_time"] = time.time()

        # 跟踪订单
        self.pending_orders[order_id] = order_response

        logger.info(f"✅ 下单成功: {order_request.side.value} {order_request.size} "
                    f"{order_request.symbol} @ {order_request.price or 'MARKET'} "
                    f"(ID: {order_id}, 延迟: {latency_ms:.1f}ms)")

        return order_response

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消订单"""
        request_data = {
            "instId": symbol,
            "ordId": order_id
        }

        success, response = await self._make_request(
            method="POST",
            endpoint=self.config.endpoints["cancel_order"],
            data=request_data,
            auth=True
        )

        if success:
            logger.info(f"订单取消成功: {order_id}")
            if order_id in self.pending_orders:
                self.pending_orders[order_id].status = OrderStatus.CANCELLED
                self.pending_orders[order_id].update_time = time.time()
            return True
        else:
            logger.error(f"订单取消失败: {order_id}")
            return False

    async def get_order_status(self, order_id: str, symbol: str) -> Optional[OrderResponse]:
        """获取订单状态"""
        params = {
            "instId": symbol,
            "ordId": order_id
        }

        success, response = await self._make_request(
            method="GET",
            endpoint=self.config.endpoints["get_order"],
            params=params,
            auth=True
        )

        if not success or not response:
            return None

        order_data = response[0] if isinstance(response, list) else response
        return self._parse_order_response(order_data)

    async def get_open_orders(self, symbol: str = None) -> List[OrderResponse]:
        """获取未成交订单"""
        params = {}
        if symbol:
            params["instId"] = symbol

        success, response = await self._make_request(
            method="GET",
            endpoint=self.config.endpoints["get_orders_pending"],
            params=params,
            auth=True
        )

        if not success or not response:
            return []

        orders = []
        for order_data in response:
            order = self._parse_order_response(order_data)
            if order:
                orders.append(order)

        return orders

    async def get_account_balance(self) -> Dict:
        """获取账户余额"""
        success, response = await self._make_request(
            method="GET",
            endpoint=self.config.endpoints["get_account_balance"],
            auth=True
        )

        if not success or not response:
            return {}

        # 解析余额数据
        balance_data = response[0] if isinstance(response, list) else response
        return {
            "total_equity": float(balance_data.get("totalEq", 0)),
            "available_balance": float(balance_data.get("availEq", 0)),
            "currency": "USDT",
            "timestamp": time.time()
        }

    async def get_positions(self, symbol: str = None) -> List[Dict]:
        """获取持仓信息"""
        params = {}
        if symbol:
            params["instId"] = symbol

        success, response = await self._make_request(
            method="GET",
            endpoint=self.config.endpoints["get_positions"],
            params=params,
            auth=True
        )

        if not success or not response:
            return []

        positions = []
        for pos_data in response:
            position = {
                "symbol": pos_data.get("instId"),
                "position_side": pos_data.get("posSide"),  # long, short
                "position_size": float(pos_data.get("pos", 0)),
                "average_price": float(pos_data.get("avgPx", 0)),
                "unrealized_pnl": float(pos_data.get("upl", 0)),
                "margin": float(pos_data.get("margin", 0)),
                "leverage": float(pos_data.get("lever", 1)),
                "liquidation_price": float(pos_data.get("liqPx", 0)),
                "timestamp": time.time()
            }
            positions.append(position)

        return positions

    def _parse_order_response(self, order_data: Dict) -> Optional[OrderResponse]:
        """解析订单响应数据"""
        try:
            # 映射状态字符串到枚举
            status_map = {
                "live": OrderStatus.LIVE,
                "partially_filled": OrderStatus.PARTIALLY_FILLED,
                "filled": OrderStatus.FILLED,
                "canceled": OrderStatus.CANCELLED,
                "rejected": OrderStatus.REJECTED,
                "expired": OrderStatus.EXPIRED
            }

            # 映射订单类型字符串到枚举
            ord_type_map = {
                "market": OrderType.MARKET,
                "limit": OrderType.LIMIT,
                "post_only": OrderType.POST_ONLY,
                "fok": OrderType.FOK,
                "ioc": OrderType.IOC
            }

            # 映射买卖方向字符串到枚举
            side_map = {
                "buy": OrderSide.BUY,
                "sell": OrderSide.SELL
            }

            status_str = order_data.get("state", "").lower()
            ord_type_str = order_data.get("ordType", "").lower()
            side_str = order_data.get("side", "").lower()

            return OrderResponse(
                order_id=order_data.get("ordId", ""),
                client_oid=order_data.get("clOrdId"),
                symbol=order_data.get("instId", ""),
                side=side_map.get(side_str, OrderSide.BUY),
                order_type=ord_type_map.get(ord_type_str, OrderType.MARKET),
                size=float(order_data.get("sz", 0)),
                price=float(order_data.get("px", 0)) if order_data.get("px") else None,
                status=status_map.get(status_str, OrderStatus.LIVE),
                filled_size=float(order_data.get("accFillSz", 0)),
                avg_fill_price=float(order_data.get("avgPx", 0)),
                fee=float(order_data.get("fee", 0)),
                created_time=int(order_data.get("cTime", 0)) / 1000,
                update_time=int(order_data.get("uTime", 0)) / 1000
            )

        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"解析订单响应失败: {e}, 数据: {order_data}")
            return None

    def get_performance_stats(self) -> Dict:
        """获取性能统计"""
        total_orders = self.stats["total_orders"]
        avg_latency = (self.stats["total_execution_latency_ms"] / total_orders
                       if total_orders > 0 else 0)

        return {
            "total_orders": total_orders,
            "successful_orders": self.stats["successful_orders"],
            "failed_orders": self.stats["failed_orders"],
            "success_rate": (self.stats["successful_orders"] / total_orders
                             if total_orders > 0 else 1.0),
            "avg_execution_latency_ms": avg_latency,
            "pending_orders": len(self.pending_orders),
            "connection_health": self.health_monitor.get_stats(),
            "last_order_time": self.stats["last_order_time"]
        }

    async def execute_market_order(self, symbol: str, side: OrderSide,
                                   size: float, reduce_only: bool = False) -> Optional[OrderResponse]:
        """执行市价单（便捷方法）"""
        order_request = OrderRequest(
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            size=size,
            reduce_only=reduce_only
        )

        return await self.place_order(order_request)

    async def execute_ioc_order(self, symbol: str, side: OrderSide,
                                size: float, price: float,
                                reduce_only: bool = False) -> Optional[OrderResponse]:
        """执行IOC订单（立即成交或取消）"""
        order_request = OrderRequest(
            symbol=symbol,
            side=side,
            order_type=OrderType.IOC,
            size=size,
            price=price,
            reduce_only=reduce_only
        )

        return await self.place_order(order_request)


class OrderManager:
    """订单状态管理器"""

    def __init__(self, executor: OKXOrderExecutor):
        self.executor = executor
        self.order_callbacks = {
            "on_filled": [],
            "on_cancelled": [],
            "on_rejected": [],
            "on_partial_fill": []
        }

    def register_callback(self, event_type: str, callback):
        """注册订单事件回调"""
        if event_type in self.order_callbacks:
            self.order_callbacks[event_type].append(callback)

    async def monitor_order(self, order_id: str, symbol: str, timeout: float = 30.0):
        """监控订单状态直到完成或超时"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            order = await self.executor.get_order_status(order_id, symbol)

            if not order:
                await asyncio.sleep(1)
                continue

            # 检查订单状态变化
            if order.status == OrderStatus.FILLED:
                await self._trigger_callbacks("on_filled", order)
                return order
            elif order.status == OrderStatus.CANCELLED:
                await self._trigger_callbacks("on_cancelled", order)
                return order
            elif order.status == OrderStatus.REJECTED:
                await self._trigger_callbacks("on_rejected", order)
                return order
            elif order.status == OrderStatus.PARTIALLY_FILLED:
                await self._trigger_callbacks("on_partial_fill", order)

            await asyncio.sleep(1)

        logger.warning(f"订单监控超时: {order_id}")
        return None

    async def _trigger_callbacks(self, event_type: str, order: OrderResponse):
        """触发回调函数"""
        for callback in self.order_callbacks.get(event_type, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(order)
                else:
                    callback(order)
            except Exception as e:
                logger.error(f"订单回调执行失败: {e}")


async def test_okx_executor():
    """测试OKX执行器"""
    print("🧪 测试OKX执行器...")

    # 创建配置（使用模拟环境）
    config = OKXAPIConfig(
        api_key="test_api_key",
        api_secret="test_api_secret",
        passphrase="test_passphrase",
        use_simulation=True
    )

    async with OKXOrderExecutor(config) as executor:
        # 测试连接
        print("1. 测试API连接...")
        balance = await executor.get_account_balance()
        print(f"   账户余额: {balance}")

        # 测试获取持仓
        print("2. 测试获取持仓...")
        positions = await executor.get_positions()
        print(f"   持仓数量: {len(positions)}")

        # 测试获取未成交订单
        print("3. 测试获取未成交订单...")
        open_orders = await executor.get_open_orders()
        print(f"   未成交订单: {len(open_orders)}")

        # 测试性能统计
        print("4. 测试性能统计...")
        stats = executor.get_performance_stats()
        print(f"   性能统计: {stats}")

        # 测试健康状态
        print("5. 测试健康状态...")
        health_stats = executor.health_monitor.get_stats()
        print(f"   健康状态: {health_stats}")

    print("✅ OKX执行器测试完成")


if __name__ == "__main__":
    asyncio.run(test_okx_executor())
