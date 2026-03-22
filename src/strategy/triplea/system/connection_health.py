#!/usr/bin/env python3
"""
连接健康检查
监控四号引擎与交易所的连接状态，提供实时健康评估和故障恢复
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import aiohttp
import psutil

from src.utils.log import get_logger

logger = get_logger(__name__)


class HealthStatus(Enum):
    """健康状态枚举"""
    HEALTHY = "healthy"  # 健康
    DEGRADED = "degraded"  # 降级
    UNHEALTHY = "unhealthy"  # 不健康
    CRITICAL = "critical"  # 严重


class ComponentType(Enum):
    """组件类型枚举"""
    API_CONNECTION = "api_connection"  # API连接
    WEBSOCKET = "websocket"  # WebSocket连接
    DATABASE = "database"  # 数据库连接
    NETWORK = "network"  # 网络连接
    SYSTEM = "system"  # 系统资源
    EXECUTION = "execution"  # 订单执行


@dataclass
class HealthMetric:
    """健康指标数据类"""
    component: ComponentType  # 组件类型
    metric_name: str  # 指标名称
    value: float  # 指标值
    unit: str  # 单位
    timestamp: float  # 时间戳
    threshold_warning: float  # 警告阈值
    threshold_critical: float  # 严重阈值


@dataclass
class ComponentHealth:
    """组件健康状态数据类"""
    component: ComponentType  # 组件类型
    status: HealthStatus  # 健康状态
    score: float  # 健康分数 (0-100)
    last_check: float  # 最后检查时间
    metrics: List[HealthMetric]  # 相关指标
    error_message: Optional[str] = None  # 错误信息
    recovery_attempts: int = 0  # 恢复尝试次数


@dataclass
class HealthCheckResult:
    """健康检查结果数据类"""
    timestamp: float  # 检查时间戳
    overall_status: HealthStatus  # 整体状态
    overall_score: float  # 整体分数
    components: Dict[ComponentType, ComponentHealth]  # 组件状态
    recommendations: List[str]  # 改进建议


class HealthCheck:
    """健康检查基类"""

    def __init__(self, component: ComponentType, check_interval: float = 10.0):
        self.component = component
        self.check_interval = check_interval
        self.last_check_time = 0.0
        self.metric_history = deque(maxlen=100)  # 保留最近100个指标

    async def check(self) -> ComponentHealth:
        """执行健康检查"""
        raise NotImplementedError

    def should_check(self) -> bool:
        """检查是否需要执行检查"""
        return time.time() - self.last_check_time >= self.check_interval

    def record_metric(self, metric: HealthMetric):
        """记录指标"""
        self.metric_history.append(metric)

    def get_metric_trend(self, metric_name: str, window: int = 10) -> Dict:
        """获取指标趋势"""
        recent_metrics = [
            m for m in list(self.metric_history)[-window:]
            if m.metric_name == metric_name
        ]

        if not recent_metrics:
            return {}

        values = [m.value for m in recent_metrics]
        timestamps = [m.timestamp for m in recent_metrics]

        return {
            "values": values,
            "timestamps": timestamps,
            "avg": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "trend": "stable" if len(values) < 2 else
            ("increasing" if values[-1] > values[0] else "decreasing")
        }


class APIConnectionCheck(HealthCheck):
    """API连接健康检查"""

    def __init__(self, base_url: str, endpoints: List[str], check_interval: float = 15.0):
        super().__init__(ComponentType.API_CONNECTION, check_interval)
        self.base_url = base_url
        self.endpoints = endpoints
        self.session: Optional[aiohttp.ClientSession] = None

    async def _create_session(self):
        """创建HTTP会话"""
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=5, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10, connect=5, sock_read=10)
            )

    async def check(self) -> ComponentHealth:
        """检查API连接"""
        await self._create_session()

        metrics = []
        success_count = 0
        total_latency = 0.0
        error_message = None

        for endpoint in self.endpoints:
            try:
                url = f"{self.base_url}{endpoint}"
                start_time = time.perf_counter()

                async with self.session.get(url) as response:
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    status_code = response.status

                    # 记录指标
                    metric = HealthMetric(
                        component=self.component,
                        metric_name=f"api_latency_{endpoint}",
                        value=latency_ms,
                        unit="ms",
                        timestamp=time.time(),
                        threshold_warning=1000.0,  # 1秒警告
                        threshold_critical=5000.0  # 5秒严重
                    )
                    metrics.append(metric)
                    self.record_metric(metric)

                    if 200 <= status_code < 300:
                        success_count += 1
                        total_latency += latency_ms
                    else:
                        error_message = f"HTTP {status_code} for {endpoint}"

            except aiohttp.ClientError as e:
                error_message = f"Client error for {endpoint}: {e}"
                metrics.append(HealthMetric(
                    component=self.component,
                    metric_name=f"api_error_{endpoint}",
                    value=1.0,
                    unit="count",
                    timestamp=time.time(),
                    threshold_warning=0.0,
                    threshold_critical=1.0
                ))
            except Exception as e:
                error_message = f"Unexpected error for {endpoint}: {e}"

        # 计算健康分数
        success_rate = success_count / len(self.endpoints) if self.endpoints else 0
        avg_latency = total_latency / success_count if success_count > 0 else float('inf')

        # 基于成功率和延迟计算分数
        latency_score = max(0, 100 - (avg_latency / 10))  # 10ms = 100分, 1000ms = 0分
        success_score = success_rate * 100
        score = (latency_score * 0.3 + success_score * 0.7)  # 加权平均

        # 确定状态
        if success_rate >= 0.95 and avg_latency < 1000:
            status = HealthStatus.HEALTHY
        elif success_rate >= 0.8 and avg_latency < 3000:
            status = HealthStatus.DEGRADED
        elif success_rate >= 0.5:
            status = HealthStatus.UNHEALTHY
        else:
            status = HealthStatus.CRITICAL

        self.last_check_time = time.time()

        return ComponentHealth(
            component=self.component,
            status=status,
            score=score,
            last_check=self.last_check_time,
            metrics=metrics,
            error_message=error_message
        )


class WebSocketCheck(HealthCheck):
    """WebSocket连接健康检查"""

    def __init__(self, ws_url: str, check_interval: float = 20.0):
        super().__init__(ComponentType.WEBSOCKET, check_interval)
        self.ws_url = ws_url
        self.last_message_time = 0.0
        self.message_count = 0

    async def check(self) -> ComponentHealth:
        """检查WebSocket连接"""
        metrics = []
        error_message = None
        score = 0.0

        try:
            # 尝试连接WebSocket
            start_time = time.perf_counter()

            # 这里使用aiohttp进行WebSocket连接测试
            # 注意：实际实现可能需要更复杂的逻辑
            connector = aiohttp.TCPConnector(ssl=False)
            timeout = aiohttp.ClientTimeout(total=10)

            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                try:
                    async with session.ws_connect(self.ws_url, heartbeat=30) as ws:
                        connect_time = (time.perf_counter() - start_time) * 1000

                        # 发送ping消息
                        await ws.ping()
                        pong = await asyncio.wait_for(ws.receive(), timeout=5)
                        ping_pong_time = (time.perf_counter() - start_time) * 1000 - connect_time

                        # 记录指标
                        metrics.append(HealthMetric(
                            component=self.component,
                            metric_name="ws_connect_time",
                            value=connect_time,
                            unit="ms",
                            timestamp=time.time(),
                            threshold_warning=1000.0,
                            threshold_critical=5000.0
                        ))

                        metrics.append(HealthMetric(
                            component=self.component,
                            metric_name="ws_ping_pong_time",
                            value=ping_pong_time,
                            unit="ms",
                            timestamp=time.time(),
                            threshold_warning=100.0,
                            threshold_critical=500.0
                        ))

                        # 计算分数
                        connect_score = max(0, 100 - (connect_time / 10))
                        ping_score = max(0, 100 - (ping_pong_time))
                        score = (connect_score * 0.4 + ping_score * 0.6)

                        if connect_time < 1000 and ping_pong_time < 100:
                            status = HealthStatus.HEALTHY
                        elif connect_time < 3000 and ping_pong_time < 500:
                            status = HealthStatus.DEGRADED
                        elif connect_time < 10000:
                            status = HealthStatus.UNHEALTHY
                        else:
                            status = HealthStatus.CRITICAL

                except asyncio.TimeoutError:
                    error_message = "WebSocket连接超时"
                    status = HealthStatus.CRITICAL
                    score = 0.0

        except Exception as e:
            error_message = f"WebSocket检查失败: {e}"
            status = HealthStatus.CRITICAL
            score = 0.0

        self.last_check_time = time.time()

        return ComponentHealth(
            component=self.component,
            status=status,
            score=score,
            last_check=self.last_check_time,
            metrics=metrics,
            error_message=error_message
        )


class SystemResourceCheck(HealthCheck):
    """系统资源健康检查"""

    def __init__(self, check_interval: float = 30.0):
        super().__init__(ComponentType.SYSTEM, check_interval)

    async def check(self) -> ComponentHealth:
        """检查系统资源"""
        metrics = []
        current_time = time.time()

        try:
            # CPU使用率
            cpu_percent = psutil.cpu_percent(interval=0.1)
            metrics.append(HealthMetric(
                component=self.component,
                metric_name="cpu_usage",
                value=cpu_percent,
                unit="%",
                timestamp=current_time,
                threshold_warning=70.0,
                threshold_critical=90.0
            ))

            # 内存使用率
            memory = psutil.virtual_memory()
            metrics.append(HealthMetric(
                component=self.component,
                metric_name="memory_usage",
                value=memory.percent,
                unit="%",
                timestamp=current_time,
                threshold_warning=80.0,
                threshold_critical=95.0
            ))

            # 磁盘使用率
            disk = psutil.disk_usage('/')
            metrics.append(HealthMetric(
                component=self.component,
                metric_name="disk_usage",
                value=disk.percent,
                unit="%",
                timestamp=current_time,
                threshold_warning=85.0,
                threshold_critical=95.0
            ))

            # 网络连接数
            connections = psutil.net_connections()
            tcp_connections = len([c for c in connections if c.status == 'ESTABLISHED'])
            metrics.append(HealthMetric(
                component=self.component,
                metric_name="tcp_connections",
                value=tcp_connections,
                unit="count",
                timestamp=current_time,
                threshold_warning=1000.0,
                threshold_critical=5000.0
            ))

            # 计算健康分数
            cpu_score = max(0, 100 - cpu_percent)
            memory_score = max(0, 100 - memory.percent)
            disk_score = max(0, 100 - disk.percent)
            connection_score = max(0, 100 - (tcp_connections / 50))  # 每50个连接扣1分

            score = (cpu_score * 0.3 + memory_score * 0.3 +
                     disk_score * 0.2 + connection_score * 0.2)

            # 确定状态
            if (cpu_percent < 70 and memory.percent < 80 and
                    disk.percent < 85 and tcp_connections < 1000):
                status = HealthStatus.HEALTHY
            elif (cpu_percent < 85 and memory.percent < 90 and
                  disk.percent < 90 and tcp_connections < 3000):
                status = HealthStatus.DEGRADED
            elif (cpu_percent < 95 and memory.percent < 95 and
                  disk.percent < 95):
                status = HealthStatus.UNHEALTHY
            else:
                status = HealthStatus.CRITICAL

        except Exception as e:
            error_message = f"系统资源检查失败: {e}"
            status = HealthStatus.CRITICAL
            score = 0.0

        self.last_check_time = current_time

        return ComponentHealth(
            component=self.component,
            status=status,
            score=score,
            last_check=self.last_check_time,
            metrics=metrics
        )


class ExecutionHealthCheck(HealthCheck):
    """订单执行健康检查"""

    def __init__(self, executor, check_interval: float = 60.0):
        super().__init__(ComponentType.EXECUTION, check_interval)
        self.executor = executor

    async def check(self) -> ComponentHealth:
        """检查订单执行健康"""
        metrics = []
        current_time = time.time()

        try:
            # 获取执行器统计信息
            if hasattr(self.executor, 'get_performance_stats'):
                stats = self.executor.get_performance_stats()

                # 成功率
                success_rate = stats.get('success_rate', 1.0)
                metrics.append(HealthMetric(
                    component=self.component,
                    metric_name="order_success_rate",
                    value=success_rate * 100,
                    unit="%",
                    timestamp=current_time,
                    threshold_warning=95.0,
                    threshold_critical=80.0
                ))

                # 平均延迟
                avg_latency = stats.get('avg_execution_latency_ms', 0)
                metrics.append(HealthMetric(
                    component=self.component,
                    metric_name="order_latency",
                    value=avg_latency,
                    unit="ms",
                    timestamp=current_time,
                    threshold_warning=1000.0,
                    threshold_critical=5000.0
                ))

                # 连续失败次数
                consecutive_failures = stats.get('connection_health', {}).get('consecutive_failures', 0)
                metrics.append(HealthMetric(
                    component=self.component,
                    metric_name="consecutive_failures",
                    value=consecutive_failures,
                    unit="count",
                    timestamp=current_time,
                    threshold_warning=3.0,
                    threshold_critical=10.0
                ))

                # 计算健康分数
                success_score = success_rate * 100
                latency_score = max(0, 100 - (avg_latency / 10))  # 10ms = 100分, 1000ms = 0分
                failure_score = max(0, 100 - (consecutive_failures * 10))

                score = (success_score * 0.5 + latency_score * 0.3 + failure_score * 0.2)

                # 确定状态
                if success_rate >= 0.95 and avg_latency < 1000 and consecutive_failures == 0:
                    status = HealthStatus.HEALTHY
                elif success_rate >= 0.8 and avg_latency < 3000 and consecutive_failures < 3:
                    status = HealthStatus.DEGRADED
                elif success_rate >= 0.5:
                    status = HealthStatus.UNHEALTHY
                else:
                    status = HealthStatus.CRITICAL

            else:
                # 执行器不可用
                status = HealthStatus.CRITICAL
                score = 0.0
                error_message = "执行器不可用"

        except Exception as e:
            error_message = f"订单执行检查失败: {e}"
            status = HealthStatus.CRITICAL
            score = 0.0

        self.last_check_time = current_time

        return ComponentHealth(
            component=self.component,
            status=status,
            score=score,
            last_check=self.last_check_time,
            metrics=metrics,
            error_message=error_message if 'error_message' in locals() else None
        )


class HealthMonitor:
    """健康监控器"""

    def __init__(self):
        self.checks: Dict[ComponentType, HealthCheck] = {}
        self.results_history = deque(maxlen=100)  # 保留最近100个检查结果
        self.running = False
        self.check_task: Optional[asyncio.Task] = None
        self.alert_handlers = []

    def add_check(self, check: HealthCheck):
        """添加健康检查"""
        self.checks[check.component] = check

    def remove_check(self, component: ComponentType):
        """移除健康检查"""
        if component in self.checks:
            del self.checks[component]

    def register_alert_handler(self, handler):
        """注册告警处理器"""
        self.alert_handlers.append(handler)

    async def start(self, check_interval: float = 30.0):
        """启动健康监控器"""
        if self.running:
            return

        self.running = True
        self.check_task = asyncio.create_task(self._monitoring_loop(check_interval))
        logger.info("健康监控器已启动")

    async def stop(self):
        """停止健康监控器"""
        if not self.running:
            return

        self.running = False

        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass

        logger.info("健康监控器已停止")

    async def _monitoring_loop(self, interval: float):
        """监控循环"""
        while self.running:
            try:
                await self.perform_health_check()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康监控循环错误: {e}")
                await asyncio.sleep(interval * 2)

    async def perform_health_check(self) -> HealthCheckResult:
        """执行健康检查"""
        components = {}
        recommendations = []

        # 执行所有检查
        for component_type, check in self.checks.items():
            if check.should_check():
                component_health = await check.check()
                components[component_type] = component_health

                # 生成建议
                if component_health.status == HealthStatus.DEGRADED:
                    recommendations.append(f"{component_type.value} 性能下降，建议优化")
                elif component_health.status == HealthStatus.UNHEALTHY:
                    recommendations.append(f"{component_type.value} 不健康，需要立即关注")
                elif component_health.status == HealthStatus.CRITICAL:
                    recommendations.append(f"{component_type.value} 严重故障，需要紧急处理")

        # 计算整体状态和分数
        if not components:
            overall_status = HealthStatus.HEALTHY
            overall_score = 100.0
        else:
            # 使用加权平均计算整体分数（关键组件权重更高）
            weights = {
                ComponentType.API_CONNECTION: 0.3,
                ComponentType.EXECUTION: 0.3,
                ComponentType.WEBSOCKET: 0.2,
                ComponentType.SYSTEM: 0.1,
                ComponentType.NETWORK: 0.1
            }

            total_weight = 0.0
            weighted_score = 0.0

            for component_type, health in components.items():
                weight = weights.get(component_type, 0.1)
                total_weight += weight
                weighted_score += health.score * weight

            overall_score = weighted_score / total_weight if total_weight > 0 else 0.0

            # 确定整体状态
            if overall_score >= 90:
                overall_status = HealthStatus.HEALTHY
            elif overall_score >= 70:
                overall_status = HealthStatus.DEGRADED
            elif overall_score >= 50:
                overall_status = HealthStatus.UNHEALTHY
            else:
                overall_status = HealthStatus.CRITICAL

        # 创建检查结果
        result = HealthCheckResult(
            timestamp=time.time(),
            overall_status=overall_status,
            overall_score=overall_score,
            components=components,
            recommendations=recommendations
        )

        # 保存历史
        self.results_history.append(result)

        # 触发告警
        await self._trigger_alerts(result)

        return result

    async def _trigger_alerts(self, result: HealthCheckResult):
        """触发告警"""
        if result.overall_status in [HealthStatus.UNHEALTHY, HealthStatus.CRITICAL]:
            for handler in self.alert_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(result)
                    else:
                        handler(result)
                except Exception as e:
                    logger.error(f"告警处理器执行失败: {e}")

    def get_recent_results(self, count: int = 10) -> List[HealthCheckResult]:
        """获取最近的检查结果"""
        return list(self.results_history)[-count:]

    def get_component_trend(self, component_type: ComponentType, window: int = 10) -> Dict:
        """获取组件趋势"""
        recent_results = self.get_recent_results(window)
        scores = []
        statuses = []
        timestamps = []

        for result in recent_results:
            if component_type in result.components:
                health = result.components[component_type]
                scores.append(health.score)
                statuses.append(health.status.value)
                timestamps.append(result.timestamp)

        return {
            "scores": scores,
            "statuses": statuses,
            "timestamps": timestamps,
            "avg_score": sum(scores) / len(scores) if scores else 0,
            "current_status": statuses[-1] if statuses else "unknown"
        }


class ConnectionHealthMonitor(HealthMonitor):
    """连接健康监控器（专门用于连接监控）"""
    pass


async def test_health_monitor():
    """测试健康监控器"""
    print("🧪 测试健康监控器...")

    # 创建健康监控器
    monitor = HealthMonitor()

    # 添加检查
    api_check = APIConnectionCheck(
        base_url="https://www.okx.com",
        endpoints=["/api/v5/public/time", "/api/v5/public/instruments"]
    )
    monitor.add_check(api_check)

    system_check = SystemResourceCheck()
    monitor.add_check(system_check)

    # 注册告警处理器
    def alert_handler(result: HealthCheckResult):
        print(f"⚠️  健康告警: {result.overall_status.value} (分数: {result.overall_score:.1f})")
        for rec in result.recommendations:
            print(f"  建议: {rec}")

    monitor.register_alert_handler(alert_handler)

    # 启动监控器
    await monitor.start(check_interval=5.0)

    # 运行几次检查
    print("1. 执行健康检查...")
    for i in range(3):
        result = await monitor.perform_health_check()
        print(f"   检查 {i + 1}: {result.overall_status.value} (分数: {result.overall_score:.1f})")
        await asyncio.sleep(5)

    # 获取趋势数据
    print("2. 获取趋势数据...")
    api_trend = monitor.get_component_trend(ComponentType.API_CONNECTION)
    print(f"   API趋势: {api_trend}")

    # 获取最近结果
    print("3. 获取最近结果...")
    recent_results = monitor.get_recent_results(3)
    print(f"   最近{len(recent_results)}个结果")

    # 停止监控器
    print("4. 停止健康监控器...")
    await monitor.stop()

    print("✅ 健康监控器测试完成")


if __name__ == "__main__":
    asyncio.run(test_health_monitor())

