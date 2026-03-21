#!/usr/bin/env python3
"""
四号引擎v3.0 实时风险监控器
监控市场风险、连接状态、仓位风险，提供实时告警和紧急处理建议
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any

from src.strategy.triplea.connection_health import HealthMonitor
from src.strategy.triplea.data_structures import RiskManagerConfig
from src.strategy.triplea.order_manager import OrderManager
from src.utils.log import get_logger

logger = get_logger(__name__)


class RiskLevel(Enum):
    """风险级别"""
    NORMAL = "normal"  # 正常
    WARNING = "warning"  # 警告
    HIGH = "high"  # 高风险
    CRITICAL = "critical"  # 危急


@dataclass
class RiskAlert:
    """风险告警"""
    alert_id: str
    component: str
    risk_level: RiskLevel
    message: str
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False
    resolved: bool = False


@dataclass
class MarketRiskMetrics:
    """市场风险指标"""
    timestamp: float
    volatility_24h: float = 0.0  # 24小时波动率
    volume_ratio: float = 0.0  # 成交量比（当前/平均）
    bid_ask_spread_pct: float = 0.0  # 买卖价差百分比
    funding_rate: float = 0.0  # 资金费率
    liq_cluster_density: float = 0.0  # 流动性集群密度


@dataclass
class PositionRiskMetrics:
    """仓位风险指标"""
    timestamp: float
    position_size: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    sl_distance_pct: float = 0.0  # 止损距离百分比
    tp_distance_pct: float = 0.0  # 止盈距离百分比
    risk_reward_ratio: float = 0.0
    drawdown_pct: float = 0.0  # 回撤百分比


class RealTimeRiskMonitor:
    """实时风险监控器

    功能：
    1. 市场风险监控：波动率、成交量、价差、资金费率
    2. 连接健康监控：API、WebSocket、系统资源
    3. 仓位风险监控：盈亏、止损距离、风险收益比
    4. 实时告警系统：分级告警，紧急处理建议
    5. 风险控制执行：自动风控动作（减仓、平仓）
    """

    def __init__(
            self,
            order_manager: OrderManager,
            risk_config: RiskManagerConfig,
            health_monitor: HealthMonitor
    ):
        """初始化实时风险监控器

        Args:
            order_manager: 订单管理器
            risk_config: 风险配置
            health_monitor: 健康监控器
        """
        self.order_manager = order_manager
        self.risk_config = risk_config
        self.health_monitor = health_monitor

        # 指标历史
        self.market_risk_history = deque(maxlen=1000)
        self.position_risk_history = deque(maxlen=1000)

        # 当前指标
        self.current_market_risk: Optional[MarketRiskMetrics] = None
        self.current_position_risk: Optional[PositionRiskMetrics] = None

        # 告警系统
        self.active_alerts: Dict[str, RiskAlert] = {}
        self.alert_history: deque[RiskAlert] = deque(maxlen=500)

        # 阈值配置
        self.thresholds = {
            "volatility_high": 0.05,  # 5% 24小时波动率
            "volume_spike": 3.0,  # 3倍平均成交量
            "spread_wide": 0.002,  # 0.2% 买卖价差
            "funding_extreme": 0.01,  # 1% 资金费率
            "pnl_drawdown": 0.03,  # 3% 回撤
            "sl_distance_close": 0.005,  # 0.5% 止损距离
            "connection_timeout": 10.0,  # 10秒连接超时
            "system_cpu_high": 80.0,  # 80% CPU使用率
            "system_memory_high": 85.0,  # 85% 内存使用率
        }

        # 统计信息
        self.stats = {
            "total_alerts": 0,
            "high_risk_alerts": 0,
            "critical_alerts": 0,
            "auto_actions": 0,
            "last_action_time": 0.0
        }

        # 运行状态
        self.running = False
        self.monitor_task: Optional[asyncio.Task] = None

        # 回调函数
        self.on_alert_callbacks: List = []
        self.on_critical_callbacks: List = []

    async def start(self):
        """启动风险监控器"""
        if self.running:
            return

        self.running = True
        self.monitor_task = asyncio.create_task(self._monitoring_loop())

        logger.info("实时风险监控器已启动")

    async def stop(self):
        """停止风险监控器"""
        if not self.running:
            return

        self.running = False

        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass

        logger.info("实时风险监控器已停止")

    async def _monitoring_loop(self):
        """监控主循环"""
        while self.running:
            try:
                # 收集所有指标
                await self._collect_metrics()

                # 分析风险
                await self._analyze_risks()

                # 检查紧急情况
                await self._check_emergencies()

                # 清理旧告警
                self._cleanup_old_alerts()

                # 更新统计
                self._update_stats()

                # 等待下一次监控
                await asyncio.sleep(1.0)  # 1秒间隔

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"风险监控循环错误: {e}")
                await asyncio.sleep(5.0)  # 错误时延长等待

    async def _collect_metrics(self):
        """收集所有风险指标"""
        try:
            # 1. 市场风险指标
            market_risk = await self._collect_market_risk()
            if market_risk:
                self.current_market_risk = market_risk
                self.market_risk_history.append(market_risk)

            # 2. 仓位风险指标
            position_risk = await self._collect_position_risk()
            if position_risk:
                self.current_position_risk = position_risk
                self.position_risk_history.append(position_risk)

            # 3. 系统健康指标（通过health_monitor）
            system_health = self.health_monitor.get_health_status()

        except Exception as e:
            logger.error(f"收集风险指标失败: {e}")

    async def _collect_market_risk(self) -> Optional[MarketRiskMetrics]:
        """收集市场风险指标"""
        try:
            # 这里应该是从市场数据API获取
            # 暂时返回模拟数据
            return MarketRiskMetrics(
                timestamp=time.time(),
                volatility_24h=0.03,  # 3%波动率
                volume_ratio=1.5,  # 1.5倍平均成交量
                bid_ask_spread_pct=0.001,  # 0.1%价差
                funding_rate=0.0005,  # 0.05%资金费率
                liq_cluster_density=0.7  # 流动性密度
            )
        except Exception as e:
            logger.error(f"收集市场风险指标失败: {e}")
            return None

    async def _collect_position_risk(self) -> Optional[PositionRiskMetrics]:
        """收集仓位风险指标"""
        try:
            # 从订单管理器获取仓位信息
            # 暂时返回模拟数据
            position_size = 0.1
            entry_price = 3000.0
            current_price = 3005.0
            stop_loss = 2998.0
            take_profit = 3012.0

            unrealized_pnl = position_size * (current_price - entry_price)
            unrealized_pnl_pct = (unrealized_pnl / (entry_price * position_size)) * 100

            sl_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
            tp_distance_pct = abs(take_profit - entry_price) / entry_price * 100
            risk_reward_ratio = tp_distance_pct / sl_distance_pct

            # 计算回撤（假设最高价为3010）
            high_price = 3010.0
            drawdown_pct = (high_price - current_price) / high_price * 100

            return PositionRiskMetrics(
                timestamp=time.time(),
                position_size=position_size,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
                sl_distance_pct=sl_distance_pct,
                tp_distance_pct=tp_distance_pct,
                risk_reward_ratio=risk_reward_ratio,
                drawdown_pct=drawdown_pct
            )
        except Exception as e:
            logger.error(f"收集仓位风险指标失败: {e}")
            return None

    async def _analyze_risks(self):
        """分析风险并触发告警"""
        try:
            # 1. 分析市场风险
            if self.current_market_risk:
                await self._analyze_market_risk(self.current_market_risk)

            # 2. 分析仓位风险
            if self.current_position_risk:
                await self._analyze_position_risk(self.current_position_risk)

            # 3. 分析系统健康风险
            health_status = self.health_monitor.get_health_status()
            await self._analyze_system_health(health_status)

        except Exception as e:
            logger.error(f"分析风险失败: {e}")

    async def _analyze_market_risk(self, metrics: MarketRiskMetrics):
        """分析市场风险"""
        alerts = []

        # 检查波动率
        if metrics.volatility_24h > self.thresholds["volatility_high"]:
            alerts.append((
                f"market_vol_high_{int(time.time())}",
                RiskLevel.HIGH,
                f"市场波动率过高: {metrics.volatility_24h:.2%}"
            ))

        # 检查成交量异常
        if metrics.volume_ratio > self.thresholds["volume_spike"]:
            alerts.append((
                f"market_volume_spike_{int(time.time())}",
                RiskLevel.WARNING,
                f"成交量异常: {metrics.volume_ratio:.1f}倍平均"
            ))

        # 检查买卖价差
        if metrics.bid_ask_spread_pct > self.thresholds["spread_wide"]:
            alerts.append((
                f"market_spread_wide_{int(time.time())}",
                RiskLevel.WARNING,
                f"买卖价差过大: {metrics.bid_ask_spread_pct:.3%}"
            ))

        # 检查资金费率
        if abs(metrics.funding_rate) > self.thresholds["funding_extreme"]:
            alerts.append((
                f"market_funding_extreme_{int(time.time())}",
                RiskLevel.HIGH,
                f"资金费率极端: {metrics.funding_rate:.3%}"
            ))

        # 触发告警
        for alert_id, risk_level, message in alerts:
            await self._trigger_alert(
                alert_id=alert_id,
                component="market",
                risk_level=risk_level,
                message=message,
                metadata={
                    "volatility": metrics.volatility_24h,
                    "volume_ratio": metrics.volume_ratio,
                    "spread": metrics.bid_ask_spread_pct,
                    "funding_rate": metrics.funding_rate
                }
            )

    async def _analyze_position_risk(self, metrics: PositionRiskMetrics):
        """分析仓位风险"""
        alerts = []

        # 检查回撤
        if metrics.drawdown_pct > self.thresholds["pnl_drawdown"]:
            alerts.append((
                f"position_drawdown_{int(time.time())}",
                RiskLevel.HIGH,
                f"仓位回撤过大: {metrics.drawdown_pct:.2%}"
            ))

        # 检查止损距离过近
        if metrics.sl_distance_pct < self.thresholds["sl_distance_close"]:
            alerts.append((
                f"position_sl_close_{int(time.time())}",
                RiskLevel.WARNING,
                f"止损距离过近: {metrics.sl_distance_pct:.3%}"
            ))

        # 检查风险收益比不足
        if metrics.risk_reward_ratio < 1.5:
            alerts.append((
                f"position_rr_low_{int(time.time())}",
                RiskLevel.WARNING,
                f"风险收益比不足: {metrics.risk_reward_ratio:.2f}"
            ))

        # 触发告警
        for alert_id, risk_level, message in alerts:
            await self._trigger_alert(
                alert_id=alert_id,
                component="position",
                risk_level=risk_level,
                message=message,
                metadata={
                    "drawdown": metrics.drawdown_pct,
                    "sl_distance": metrics.sl_distance_pct,
                    "risk_reward_ratio": metrics.risk_reward_ratio,
                    "unrealized_pnl": metrics.unrealized_pnl
                }
            )

    async def _analyze_system_health(self, health_status: Dict):
        """分析系统健康风险"""
        for component, status in health_status.items():
            if status.get("status") != "healthy":
                # 根据严重程度设置风险级别
                if component in ["api_connection", "websocket"]:
                    risk_level = RiskLevel.CRITICAL
                elif component in ["memory", "cpu"]:
                    risk_level = RiskLevel.HIGH
                else:
                    risk_level = RiskLevel.WARNING

                await self._trigger_alert(
                    alert_id=f"system_{component}_{int(time.time())}",
                    component="system",
                    risk_level=risk_level,
                    message=f"系统组件异常: {component} - {status.get('message', '未知错误')}",
                    metadata={
                        "component": component,
                        "status": status.get("status"),
                        "message": status.get("message"),
                        "last_check": status.get("last_check")
                    }
                )

    async def _check_emergencies(self):
        """检查紧急情况并触发自动处理"""
        try:
            # 检查危急告警
            critical_alerts = [
                alert for alert in self.active_alerts.values()
                if alert.risk_level == RiskLevel.CRITICAL and not alert.resolved
            ]

            for alert in critical_alerts:
                # 如果告警持续超过30秒，触发紧急处理
                if time.time() - alert.timestamp > 30.0 and not alert.acknowledged:
                    logger.warning(f"🚨 触发紧急处理: {alert.message}")

                    # 触发紧急处理回调
                    await self._trigger_critical_action(alert)

                    # 标记为已处理
                    alert.acknowledged = True

                    self.stats["auto_actions"] += 1
                    self.stats["last_action_time"] = time.time()

        except Exception as e:
            logger.error(f"检查紧急情况失败: {e}")

    async def _trigger_alert(
            self,
            alert_id: str,
            component: str,
            risk_level: RiskLevel,
            message: str,
            metadata: Dict[str, Any] = None
    ):
        """触发风险告警"""
        # 检查是否已有相同告警
        if alert_id in self.active_alerts:
            existing = self.active_alerts[alert_id]
            if existing.resolved or existing.acknowledged:
                # 重新激活
                existing.resolved = False
                existing.acknowledged = False
                existing.timestamp = time.time()
                existing.message = message
                existing.metadata.update(metadata or {})
            return

        # 创建新告警
        alert = RiskAlert(
            alert_id=alert_id,
            component=component,
            risk_level=risk_level,
            message=message,
            timestamp=time.time(),
            metadata=metadata or {}
        )

        # 添加到活跃告警
        self.active_alerts[alert_id] = alert
        self.alert_history.append(alert)

        # 更新统计
        self.stats["total_alerts"] += 1
        if risk_level == RiskLevel.HIGH:
            self.stats["high_risk_alerts"] += 1
        elif risk_level == RiskLevel.CRITICAL:
            self.stats["critical_alerts"] += 1

        # 触发回调
        await self._notify_alert(alert)

        # 根据风险级别打印日志
        if risk_level == RiskLevel.CRITICAL:
            logger.critical(f"🚨 危急告警: {message}")
        elif risk_level == RiskLevel.HIGH:
            logger.error(f"⚠️ 高风险告警: {message}")
        elif risk_level == RiskLevel.WARNING:
            logger.warning(f"🔶 警告告警: {message}")
        else:
            logger.info(f"ℹ️ 普通告警: {message}")

    async def _trigger_critical_action(self, alert: RiskAlert):
        """触发危急情况的自动处理"""
        try:
            # 根据组件类型采取不同措施
            if alert.component == "market":
                # 市场异常，建议减仓或平仓
                logger.info("📉 市场异常，建议减仓")
                # 这里可以调用仓位管理器进行减仓操作

            elif alert.component == "system":
                if "connection" in alert.metadata.get("component", ""):
                    # 连接异常，可能需要切换备用连接或重启
                    logger.info("🔌 连接异常，检查备用连接")

            # 触发回调
            for callback in self.on_critical_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(alert)
                    else:
                        callback(alert)
                except Exception as e:
                    logger.error(f"危急回调执行失败: {e}")

        except Exception as e:
            logger.error(f"触发危急处理失败: {e}")

    async def _notify_alert(self, alert: RiskAlert):
        """通知告警回调"""
        for callback in self.on_alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert)
                else:
                    callback(alert)
            except Exception as e:
                logger.error(f"告警告警回调执行失败: {e}")

    def _cleanup_old_alerts(self):
        """清理旧告警"""
        current_time = time.time()
        to_remove = []

        for alert_id, alert in self.active_alerts.items():
            # 如果告警已解决且超过5分钟，或未解决但超过1小时
            if (alert.resolved and current_time - alert.timestamp > 300) or \
                    (not alert.resolved and current_time - alert.timestamp > 3600):
                to_remove.append(alert_id)

        for alert_id in to_remove:
            del self.active_alerts[alert_id]

    def _update_stats(self):
        """更新统计信息"""
        # 统计当前活跃告警
        active_counts = {
            "normal": 0,
            "warning": 0,
            "high": 0,
            "critical": 0
        }

        for alert in self.active_alerts.values():
            if not alert.resolved:
                active_counts[alert.risk_level.value] += 1

        # 更新统计
        self.stats["active_alerts"] = {
            "total": sum(active_counts.values()),
            "by_level": active_counts
        }

    def acknowledge_alert(self, alert_id: str):
        """确认告警"""
        if alert_id in self.active_alerts:
            self.active_alerts[alert_id].acknowledged = True
            logger.info(f"✅ 告警已确认: {alert_id}")

    def resolve_alert(self, alert_id: str):
        """解决告警"""
        if alert_id in self.active_alerts:
            self.active_alerts[alert_id].resolved = True
            logger.info(f"✅ 告警已解决: {alert_id}")

    def register_alert_callback(self, callback):
        """注册告警报知回调"""
        self.on_alert_callbacks.append(callback)

    def register_critical_callback(self, callback):
        """注册危急情况回调"""
        self.on_critical_callbacks.append(callback)

    def get_active_alerts(self) -> List[RiskAlert]:
        """获取活跃告警"""
        return [
            alert for alert in self.active_alerts.values()
            if not alert.resolved
        ]

    def get_alert_history(self, limit: int = 100) -> List[RiskAlert]:
        """获取告警历史"""
        return list(self.alert_history)[-limit:]

    def get_risk_summary(self) -> Dict[str, Any]:
        """获取风险摘要"""
        active_alerts = self.get_active_alerts()

        return {
            "current_risk_level": self._calculate_overall_risk_level(),
            "active_alerts_count": len(active_alerts),
            "high_risk_alerts": len([a for a in active_alerts if a.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]]),
            "market_risk": self.current_market_risk.__dict__ if self.current_market_risk else {},
            "position_risk": self.current_position_risk.__dict__ if self.current_position_risk else {},
            "system_health": self.health_monitor.get_health_summary(),
            "statistics": self.stats
        }

    def _calculate_overall_risk_level(self) -> RiskLevel:
        """计算整体风险级别"""
        active_alerts = self.get_active_alerts()

        if any(alert.risk_level == RiskLevel.CRITICAL for alert in active_alerts):
            return RiskLevel.CRITICAL
        elif any(alert.risk_level == RiskLevel.HIGH for alert in active_alerts):
            return RiskLevel.HIGH
        elif any(alert.risk_level == RiskLevel.WARNING for alert in active_alerts):
            return RiskLevel.WARNING
        else:
            return RiskLevel.NORMAL

    def update_market_risk(self, volatility_24h: float, volume_ratio: float,
                           bid_ask_spread_pct: float, funding_rate: float,
                           liq_cluster_density: float = 0.7):
        """更新市场风险指标

        Args:
            volatility_24h: 24小时波动率
            volume_ratio: 成交量比（当前/平均）
            bid_ask_spread_pct: 买卖价差百分比
            funding_rate: 资金费率
            liq_cluster_density: 流动性集群密度
        """
        market_risk = MarketRiskMetrics(
            timestamp=time.time(),
            volatility_24h=volatility_24h,
            volume_ratio=volume_ratio,
            bid_ask_spread_pct=bid_ask_spread_pct,
            funding_rate=funding_rate,
            liq_cluster_density=liq_cluster_density
        )
        self.current_market_risk = market_risk
        self.market_risk_history.append(market_risk)

        # 立即分析风险
        asyncio.create_task(self._analyze_market_risk(market_risk))

        logger.debug(f"市场风险指标已更新: 波动率={volatility_24h:.2%}, 成交量比={volume_ratio:.1f}")

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            **self.stats,
            "market_risk_history_size": len(self.market_risk_history),
            "position_risk_history_size": len(self.position_risk_history),
            "alert_history_size": len(self.alert_history)
        }


async def test_real_time_risk_monitor():
    """测试实时风险监控器"""
    print("🧪 测试实时风险监控器")
    print("=" * 60)

    from src.strategy.triplea.data_structures import RiskManagerConfig

    # 创建配置
    risk_config = RiskManagerConfig(
        account_size_usdt=300.0,
        max_risk_per_trade_pct=5.0,
        stop_loss_ticks=2,
        take_profit_ticks=6,
        max_daily_loss_pct=5.0
    )

    # 创建模拟的健康监控器
    class MockHealthMonitor:
        def get_health_status(self):
            return {
                "api_connection": {"status": "healthy", "last_check": time.time()},
                "websocket": {"status": "unhealthy", "message": "连接超时", "last_check": time.time()},
                "memory": {"status": "warning", "message": "内存使用率75%", "last_check": time.time()},
                "cpu": {"status": "healthy", "last_check": time.time()}
            }

        def get_health_summary(self):
            return {"overall": "warning", "details": self.get_health_status()}

    # 创建模拟的订单管理器
    class MockOrderManager:
        pass

    # 创建监控器
    health_monitor = MockHealthMonitor()
    order_manager = MockOrderManager()
    risk_monitor = RealTimeRiskMonitor(order_manager, risk_config, health_monitor)

    # 注册回调
    def on_alert_callback(alert):
        print(f"🔔 收到告警: {alert.message} (级别: {alert.risk_level.value})")

    def on_critical_callback(alert):
        print(f"🚨 收到危急告警: {alert.message}")

    risk_monitor.register_alert_callback(on_alert_callback)
    risk_monitor.register_critical_callback(on_critical_callback)

    # 启动监控器
    await risk_monitor.start()

    # 等待一段时间收集数据
    print("\n📊 等待数据收集...")
    await asyncio.sleep(3)

    # 获取风险摘要
    print("\n📊 风险摘要:")
    risk_summary = risk_monitor.get_risk_summary()
    for key, value in risk_summary.items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k, v in value.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {key}: {value}")

    # 获取活跃告警
    print("\n⚠️ 活跃告警:")
    active_alerts = risk_monitor.get_active_alerts()
    for alert in active_alerts:
        print(f"  • {alert.component}: {alert.message} (级别: {alert.risk_level.value})")

    # 获取统计信息
    print("\n📈 统计信息:")
    stats = risk_monitor.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # 停止监控器
    await risk_monitor.stop()

    print("\n✅ 实时风险监控器测试完成")


if __name__ == "__main__":
    asyncio.run(test_real_time_risk_monitor())
