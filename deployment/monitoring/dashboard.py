#!/usr/bin/env python3
"""
四号引擎生产环境监控仪表板
集成指标收集、告警和可视化功能
"""

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    import psutil
    import yaml
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.text import Text
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.syntax import Syntax

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("⚠️  Rich库未安装，将使用简单文本输出")

from src.utils.log import get_logger
from deployment.monitoring.metrics import MetricsCollector
from deployment.monitoring.alerts import AlertManager, AlertSeverity

# 尝试导入四号引擎组件
try:
    from src.strategy.triplea.signal_generator import TripleASignalGenerator

    SIGNAL_GENERATOR_AVAILABLE = True
except ImportError as e:
    SIGNAL_GENERATOR_AVAILABLE = False
    logger.warning(f"无法导入TripleASignalGenerator: {e}")

# 尝试获取全局signal_generator实例（如果存在）
try:
    # 这里可以添加获取全局实例的逻辑
    # 例如，从orchestrator导入或使用单例模式
    pass
except Exception:
    pass

logger = get_logger(__name__)


@dataclass
class DashboardConfig:
    """仪表板配置"""
    refresh_interval: float = 1.0  # 刷新间隔（秒）
    history_size: int = 1000  # 历史记录大小
    enable_prometheus: bool = True  # 启用Prometheus
    prometheus_port: int = 9091  # Prometheus端口
    enable_alerts: bool = True  # 启用告警
    alert_config_path: Optional[str] = None  # 告警配置路径
    log_level: str = "INFO"  # 日志级别


class MonitoringDashboard:
    """监控仪表板"""

    def __init__(self, config: DashboardConfig):
        """
        初始化监控仪表板

        Args:
            config: 仪表板配置
        """
        self.config = config
        self.console = Console() if RICH_AVAILABLE else None

        # 初始化组件
        self.metrics_collector = MetricsCollector(history_size=config.history_size)
        self.alert_manager = AlertManager(config.alert_config_path)

        # 四号引擎signal_generator实例（如果可用）
        self.signal_generator = None
        if SIGNAL_GENERATOR_AVAILABLE:
            # 这里可以尝试获取全局实例
            # 暂时留空，后续可以通过其他方式设置
            pass

        # 状态跟踪
        self.is_running = False
        self.start_time = time.time()
        self.update_count = 0

        # 性能统计
        self.performance_stats = {
            "avg_refresh_time": 0,
            "max_refresh_time": 0,
            "min_refresh_time": float('inf'),
            "total_refresh_time": 0
        }

        # 初始化Prometheus服务器
        if config.enable_prometheus:
            self._start_prometheus_server()

    def set_signal_generator(self, signal_generator):
        """设置四号引擎signal_generator实例

        Args:
            signal_generator: TripleASignalGenerator实例
        """
        if SIGNAL_GENERATOR_AVAILABLE and isinstance(signal_generator, TripleASignalGenerator):
            self.signal_generator = signal_generator
            logger.info("✅ 已设置四号引擎signal_generator实例")
        else:
            logger.warning(f"⚠️  无法设置signal_generator实例: 类型不匹配或不可用")

    def _start_prometheus_server(self):
        """启动Prometheus服务器"""
        try:
            self.metrics_collector.start_prometheus_server(port=self.config.prometheus_port)
            logger.info(f"✅ Prometheus指标服务器启动在端口 {self.config.prometheus_port}")
        except Exception as e:
            logger.error(f"❌ 启动Prometheus服务器失败: {e}")

    def collect_metrics(self):
        """收集所有指标"""
        try:
            # 收集系统指标
            system_metrics = self.metrics_collector.collect_system_metrics()

            # 从四号引擎获取性能指标
            performance_metrics = self._get_performance_metrics()

            # 从四号引擎获取业务指标
            business_metrics = self._get_business_metrics()

            # 更新指标收集器
            if performance_metrics:
                self.metrics_collector.collect_performance_metrics(**performance_metrics)

            if business_metrics:
                self.metrics_collector.collect_business_metrics(**business_metrics)

            # 更新告警系统
            if self.config.enable_alerts:
                self._update_alert_system(system_metrics)

            return True

        except Exception as e:
            logger.error(f"❌ 收集指标失败: {e}")
            return False

    def _update_alert_system(self, system_metrics):
        """更新告警系统"""
        try:
            # 更新系统指标到告警系统
            self.alert_manager.update_metric("system.cpu_percent", system_metrics.cpu_percent)
            self.alert_manager.update_metric("system.memory_percent", system_metrics.memory_percent)
            self.alert_manager.update_metric("system.disk_usage_percent", system_metrics.disk_usage_percent)

            # 这里可以添加更多指标更新

        except Exception as e:
            logger.error(f"❌ 更新告警系统失败: {e}")

    def _get_performance_metrics(self) -> Dict[str, Any]:
        """从四号引擎获取性能指标

        返回:
            Dict[str, Any]: 性能指标字典，包含：
                - tick_latency_ms: Tick处理延迟（毫秒）
                - cvd_computation_ms: CVD计算延迟（毫秒）
                - kde_computation_ms: KDE计算延迟（毫秒）
                - state_transition_ms: 状态转换延迟（毫秒）
                - queue_depth: 任务队列深度
                - cache_hit_rate: 缓存命中率
        """
        try:
            if not self.signal_generator:
                # 如果没有signal_generator实例，返回默认值
                return {
                    'tick_latency_ms': 0.0,
                    'cvd_computation_ms': 0.0,
                    'kde_computation_ms': 0.0,
                    'state_transition_ms': 0.0,
                    'queue_depth': 0,
                    'cache_hit_rate': 0.0
                }

            # 从signal_generator获取性能统计
            stats = self.signal_generator.get_performance_stats()

            # 提取性能指标
            performance_metrics = {
                'tick_latency_ms': 0.0,
                'cvd_computation_ms': 0.0,
                'kde_computation_ms': 0.0,
                'state_transition_ms': 0.0,
                'queue_depth': 0,
                'cache_hit_rate': 0.0
            }

            # 从状态机获取平均处理时间（转换为毫秒）
            if 'state_machine' in stats and 'avg_processing_time_ns' in stats['state_machine']:
                performance_metrics['tick_latency_ms'] = stats['state_machine']['avg_processing_time_ns'] / 1_000_000

            # 从CVD计算器获取处理时间
            if 'cvd_calculator' in stats and 'total_processing_time_ns' in stats['cvd_calculator']:
                cvd_ticks = stats['cvd_calculator'].get('ticks_processed', 1)
                if cvd_ticks > 0:
                    performance_metrics['cvd_computation_ms'] = (
                            stats['cvd_calculator']['total_processing_time_ns'] / cvd_ticks / 1_000_000
                    )

            # 从KDE引擎获取处理时间
            if 'kde_engine' in stats and 'avg_kde_time_ms' in stats['kde_engine']:
                performance_metrics['kde_computation_ms'] = stats['kde_engine']['avg_kde_time_ms']

            # 从状态转换统计估算状态转换延迟
            if 'state_machine' in stats and 'state_transitions' in stats['state_machine']:
                state_transitions = stats['state_machine']['state_transitions']
                if state_transitions > 0:
                    # 假设每次状态转换平均需要0.1ms
                    performance_metrics['state_transition_ms'] = 0.1

            # 从JIT监控器获取缓存命中率（如果可用）
            try:
                from src.strategy.triplea.jit_monitor import JITMonitor
                # 这里可以添加获取JIT监控器实例的逻辑
                # performance_metrics['cache_hit_rate'] = jit_monitor.get_cache_hit_rate()
            except ImportError:
                pass

            return performance_metrics

        except Exception as e:
            logger.warning(f"获取性能指标失败: {e}")
            # 返回默认值
            return {
                'tick_latency_ms': 0.0,
                'cvd_computation_ms': 0.0,
                'kde_computation_ms': 0.0,
                'state_transition_ms': 0.0,
                'queue_depth': 0,
                'cache_hit_rate': 0.0
            }

    def _get_business_metrics(self) -> Dict[str, Any]:
        """从四号引擎获取业务指标

        返回:
            Dict[str, Any]: 业务指标字典，包含：
                - tick_count: 处理的Tick数量
                - order_count: 订单数量
                - signal_count: 信号数量
                - error_count: 错误数量
                - current_state: 当前状态机状态
                - position_pnl: 持仓盈亏
                - account_balance: 账户余额
                - risk_exposure: 风险暴露
        """
        try:
            if not self.signal_generator:
                # 如果没有signal_generator实例，返回默认值
                return {
                    'tick_count': 0,
                    'order_count': 0,
                    'signal_count': 0,
                    'error_count': 0,
                    'current_state': "IDLE",
                    'position_pnl': 0.0,
                    'account_balance': 0.0,
                    'risk_exposure': 0.0
                }

            # 从signal_generator获取性能统计
            stats = self.signal_generator.get_performance_stats()

            # 提取业务指标
            business_metrics = {
                'tick_count': 0,
                'order_count': 0,
                'signal_count': 0,
                'error_count': 0,
                'current_state': "IDLE",
                'position_pnl': 0.0,
                'account_balance': 0.0,
                'risk_exposure': 0.0
            }

            # 从signal_generator获取处理的Tick数
            if 'signal_generator' in stats and 'processed_ticks' in stats['signal_generator']:
                business_metrics['tick_count'] = stats['signal_generator']['processed_ticks']

            # 从状态机获取当前状态
            if 'state_machine' in stats and 'current_state' in stats['state_machine']:
                business_metrics['current_state'] = stats['state_machine']['current_state']

            # 从状态机获取状态转换次数（作为信号数量的代理）
            if 'state_machine' in stats and 'state_transitions' in stats['state_machine']:
                business_metrics['signal_count'] = stats['state_machine']['state_transitions']

            # 从状态机获取触发事件次数
            if 'state_machine' in stats and 'events_triggered' in stats['state_machine']:
                business_metrics['signal_count'] = max(
                    business_metrics['signal_count'],
                    stats['state_machine']['events_triggered']
                )

            # 尝试从状态机上下文获取持仓信息
            try:
                if hasattr(self.signal_generator.state_machine, 'context'):
                    context = self.signal_generator.state_machine.context

                    # 如果有持仓，计算盈亏（简化版）
                    if hasattr(context, 'trade_direction') and context.trade_direction:
                        # 这里需要实际的价格数据来计算盈亏
                        # 暂时使用0.0
                        business_metrics['position_pnl'] = 0.0

                        # 风险暴露（基于仓位大小）
                        if hasattr(context, 'entry_price') and context.entry_price > 0:
                            # 简化风险暴露计算
                            business_metrics['risk_exposure'] = 0.05  # 假设5%风险暴露
            except Exception as e:
                logger.debug(f"获取持仓信息失败: {e}")

            # 账户余额（需要从交易执行器获取，这里使用默认值）
            business_metrics['account_balance'] = 300.0  # 默认300U小资金

            return business_metrics

        except Exception as e:
            logger.warning(f"获取业务指标失败: {e}")
            # 返回默认值
            return {
                'tick_count': 0,
                'order_count': 0,
                'signal_count': 0,
                'error_count': 0,
                'current_state': "IDLE",
                'position_pnl': 0.0,
                'account_balance': 0.0,
                'risk_exposure': 0.0
            }

    def get_system_summary(self) -> Dict[str, Any]:
        """获取系统摘要"""
        return self.metrics_collector.get_system_summary()

    def get_performance_summary(self) -> Dict[str, Any]:
        """获取性能摘要"""
        return self.metrics_collector.get_performance_summary()

    def get_business_summary(self) -> Dict[str, Any]:
        """获取业务摘要"""
        return self.metrics_collector.get_business_summary()

    def get_alerts_summary(self) -> Dict[str, Any]:
        """获取告警摘要"""
        active_alerts = self.alert_manager.get_active_alerts()
        alert_history = self.alert_manager.get_alert_history(10)

        return {
            "active_alerts": len(active_alerts),
            "recent_alerts": len(alert_history),
            "critical_alerts": len([a for a in active_alerts if a.rule.severity == AlertSeverity.CRITICAL]),
            "error_alerts": len([a for a in active_alerts if a.rule.severity == AlertSeverity.ERROR]),
            "warning_alerts": len([a for a in active_alerts if a.rule.severity == AlertSeverity.WARNING])
        }

    def get_dashboard_data(self) -> Dict[str, Any]:
        """获取仪表板数据"""
        return {
            "system": self.get_system_summary(),
            "performance": self.get_performance_summary(),
            "business": self.get_business_summary(),
            "alerts": self.get_alerts_summary(),
            "uptime": time.time() - self.start_time,
            "update_count": self.update_count,
            "refresh_stats": self.performance_stats
        }

    def display_rich_dashboard(self):
        """显示Rich仪表板"""
        if not RICH_AVAILABLE:
            self.display_text_dashboard()
            return

        # 创建布局
        layout = Layout()

        # 分割布局
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=3)
        )

        # 主区域分割
        layout["main"].split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=1)
        )

        # 左侧区域分割
        layout["left"].split(
            Layout(name="system", size=8),
            Layout(name="performance", size=8),
            Layout(name="business")
        )

        # 右侧区域分割
        layout["right"].split(
            Layout(name="alerts", size=12),
            Layout(name="status")
        )

        # 获取数据
        data = self.get_dashboard_data()

        # 更新各个面板
        layout["header"].update(self._create_header_panel(data))
        layout["system"].update(self._create_system_panel(data["system"]))
        layout["performance"].update(self._create_performance_panel(data["performance"]))
        layout["business"].update(self._create_business_panel(data["business"]))
        layout["alerts"].update(self._create_alerts_panel(data["alerts"]))
        layout["status"].update(self._create_status_panel(data))
        layout["footer"].update(self._create_footer_panel(data))

        # 清屏并显示
        self.console.clear()
        self.console.print(layout)

    def _create_header_panel(self, data: Dict[str, Any]):
        """创建标题面板"""
        title = Text("🚀 四号引擎生产环境监控仪表板", style="bold blue")
        subtitle = Text(f"运行时间: {timedelta(seconds=int(data['uptime']))} | "
                        f"更新次数: {data['update_count']} | "
                        f"刷新延迟: {data['refresh_stats']['avg_refresh_time']:.1f}ms",
                        style="dim")

        header_text = Text()
        header_text.append(title)
        header_text.append("\n")
        header_text.append(subtitle)

        return Panel(header_text, border_style="blue")

    def _create_system_panel(self, system_data: Dict[str, Any]):
        """创建系统面板"""
        if not system_data:
            return Panel("暂无系统数据", title="🖥️ 系统状态", border_style="cyan")

        table = Table(show_header=False, box=None)
        table.add_column("指标", style="cyan")
        table.add_column("值", style="white")

        table.add_row("CPU使用率", f"{system_data.get('cpu_percent', 0):.1f}%")
        table.add_row("内存使用", f"{system_data.get('memory_used_mb', 0):.1f} MB")
        table.add_row("磁盘使用", f"{system_data.get('disk_usage_percent', 0):.1f}%")
        table.add_row("系统负载", f"{system_data.get('load_average', (0, 0, 0))[0]:.2f}")
        table.add_row("进程数量", str(system_data.get('process_count', 0)))
        table.add_row("更新时间", system_data.get('timestamp', 'N/A'))

        return Panel(table, title="🖥️ 系统状态", border_style="cyan")

    def _create_performance_panel(self, perf_data: Dict[str, Any]):
        """创建性能面板"""
        if not perf_data:
            return Panel("暂无性能数据", title="⚡ 性能指标", border_style="green")

        table = Table(show_header=False, box=None)
        table.add_column("指标", style="green")
        table.add_column("值", style="white")

        table.add_row("平均延迟", f"{perf_data.get('avg_total_latency_ms', 0):.3f} ms")
        table.add_row("最大延迟", f"{perf_data.get('max_total_latency_ms', 0):.3f} ms")
        table.add_row("最小延迟", f"{perf_data.get('min_total_latency_ms', 0):.3f} ms")
        table.add_row("缓存命中率", f"{perf_data.get('avg_cache_hit_rate', 0):.1%}")
        table.add_row("样本数量", str(perf_data.get('sample_count', 0)))

        # 延迟状态指示
        avg_latency = perf_data.get('avg_total_latency_ms', 0)
        if avg_latency < 1:
            latency_status = "✅ 优秀"
        elif avg_latency < 5:
            latency_status = "⚠️ 良好"
        else:
            latency_status = "❌ 需优化"

        table.add_row("延迟状态", latency_status)

        return Panel(table, title="⚡ 性能指标", border_style="green")

    def _create_business_panel(self, business_data: Dict[str, Any]):
        """创建业务面板"""
        if not business_data:
            return Panel("暂无业务数据", title="💰 业务指标", border_style="yellow")

        table = Table(show_header=False, box=None)
        table.add_column("指标", style="yellow")
        table.add_column("值", style="white")

        table.add_row("当前状态", business_data.get('current_state', 'N/A'))
        table.add_row("总Tick数", str(business_data.get('total_ticks', 0)))
        table.add_row("总信号数", str(business_data.get('total_signals', 0)))
        table.add_row("总错误数", str(business_data.get('total_errors', 0)))
        table.add_row("持仓盈亏", f"{business_data.get('position_pnl', 0):.2f} USDT")
        table.add_row("账户余额", f"{business_data.get('account_balance', 0):.2f} USDT")
        table.add_row("风险暴露", f"{business_data.get('risk_exposure', 0):.1%}")

        return Panel(table, title="💰 业务指标", border_style="yellow")

    def _create_alerts_panel(self, alerts_data: Dict[str, Any]):
        """创建告警面板"""
        active_alerts = self.alert_manager.get_active_alerts()

        if not active_alerts:
            return Panel("✅ 无活跃告警", title="🚨 告警状态", border_style="red")

        table = Table(box=None)
        table.add_column("严重程度", style="red")
        table.add_column("规则", style="white")
        table.add_column("消息", style="dim")

        for alert in active_alerts[:5]:  # 显示最多5个告警
            severity_emoji = {
                AlertSeverity.CRITICAL: "🚨",
                AlertSeverity.ERROR: "❌",
                AlertSeverity.WARNING: "⚠️",
                AlertSeverity.INFO: "ℹ️"
            }

            emoji = severity_emoji.get(alert.rule.severity, "📢")
            table.add_row(f"{emoji} {alert.rule.severity.value}", alert.rule.name, alert.message)

        if len(active_alerts) > 5:
            table.add_row("...", f"还有 {len(active_alerts) - 5} 个告警", "...")

        return Panel(table, title="🚨 告警状态", border_style="red")

    def _create_status_panel(self, data: Dict[str, Any]):
        """创建状态面板"""
        table = Table(show_header=False, box=None)
        table.add_column("组件", style="magenta")
        table.add_column("状态", style="white")

        # 系统状态
        system_ok = data["system"].get("cpu_percent", 0) < 90
        table.add_row("系统", "✅ 正常" if system_ok else "❌ 异常")

        # 性能状态
        perf_ok = data["performance"].get("avg_total_latency_ms", 0) < 5
        table.add_row("性能", "✅ 正常" if perf_ok else "❌ 异常")

        # 业务状态
        biz_ok = data["business"].get("total_errors", 0) < 10
        table.add_row("业务", "✅ 正常" if biz_ok else "❌ 异常")

        # 告警状态
        alerts_ok = data["alerts"].get("critical_alerts", 0) == 0
        table.add_row("告警", "✅ 正常" if alerts_ok else "❌ 异常")

        # Prometheus状态
        prometheus_ok = self.config.enable_prometheus
        table.add_row("Prometheus", "✅ 启用" if prometheus_ok else "❌ 禁用")

        return Panel(table, title="📊 组件状态", border_style="magenta")

    def _create_footer_panel(self, data: Dict[str, Any]):
        """创建页脚面板"""
        refresh_stats = data["refresh_stats"]
        footer_text = Text()

        # 性能统计
        stats_line = f"刷新统计: 平均 {refresh_stats['avg_refresh_time']:.1f}ms, "
        stats_line += f"最大 {refresh_stats['max_refresh_time']:.1f}ms, "
        stats_line += f"最小 {refresh_stats['min_refresh_time']:.1f}ms"

        # 时间信息
        time_line = f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        time_line += f"下次刷新: {self.config.refresh_interval:.1f}s"

        footer_text.append(stats_line, style="dim")
        footer_text.append(" | ", style="dim")
        footer_text.append(time_line, style="dim")

        return Panel(footer_text, border_style="dim")

    def display_text_dashboard(self):
        """显示文本仪表板（当Rich不可用时）"""
        data = self.get_dashboard_data()

        print("\n" + "=" * 80)
        print("🚀 四号引擎生产环境监控仪表板")
        print("=" * 80)

        # 系统状态
        print("\\n🖥️ 系统状态:")
        if data["system"]:
            for key, value in data["system"].items():
                print(f"  {key}: {value}")
        else:
            print("  暂无数据")

        # 性能指标
        print("\\n⚡ 性能指标:")
        if data["performance"]:
            for key, value in data["performance"].items():
                print(f"  {key}: {value}")
        else:
            print("  暂无数据")

        # 业务指标
        print("\\n💰 业务指标:")
        if data["business"]:
            for key, value in data["business"].items():
                print(f"  {key}: {value}")
        else:
            print("  暂无数据")

        # 告警状态
        print("\\n🚨 告警状态:")
        alerts_data = data["alerts"]
        print(f"  活跃告警: {alerts_data.get('active_alerts', 0)}")
        print(f"  严重告警: {alerts_data.get('critical_alerts', 0)}")
        print(f"  错误告警: {alerts_data.get('error_alerts', 0)}")
        print(f"  警告告警: {alerts_data.get('warning_alerts', 0)}")

        # 活跃告警详情
        active_alerts = self.alert_manager.get_active_alerts()
        if active_alerts:
            print("\\n  活跃告警详情:")
            for alert in active_alerts[:3]:  # 显示最多3个
                print(f"    - {alert.rule.name}: {alert.message}")

        # 状态统计
        print("\\n📊 状态统计:")
        print(f"  运行时间: {timedelta(seconds=int(data['uptime']))}")
        print(f"  更新次数: {data['update_count']}")
        print(f"  平均刷新延迟: {data['refresh_stats']['avg_refresh_time']:.1f}ms")

        print("\\n" + "=" * 80)

    async def run(self):
        """运行监控仪表板"""
        self.is_running = True
        self.start_time = time.time()

        logger.info("🚀 启动监控仪表板...")
        logger.info(f"刷新间隔: {self.config.refresh_interval}秒")
        logger.info(f"启用Prometheus: {self.config.enable_prometheus}")
        logger.info(f"启用告警: {self.config.enable_alerts}")

        try:
            while self.is_running:
                start_time = time.time()

                # 收集指标
                success = self.collect_metrics()
                if success:
                    self.update_count += 1

                # 显示仪表板
                if RICH_AVAILABLE:
                    self.display_rich_dashboard()
                else:
                    self.display_text_dashboard()

                # 更新性能统计
                refresh_time = (time.time() - start_time) * 1000  # 转换为毫秒
                self._update_performance_stats(refresh_time)

                # 等待下一个刷新周期
                await asyncio.sleep(self.config.refresh_interval)

        except KeyboardInterrupt:
            logger.info("👋 收到中断信号，正在停止...")
        except Exception as e:
            logger.error(f"❌ 仪表板运行出错: {e}")
        finally:
            self.is_running = False
            logger.info("🛑 监控仪表板已停止")

    def _update_performance_stats(self, refresh_time: float):
        """更新性能统计"""
        stats = self.performance_stats

        stats["total_refresh_time"] += refresh_time
        stats["avg_refresh_time"] = stats["total_refresh_time"] / self.update_count if self.update_count > 0 else 0
        stats["max_refresh_time"] = max(stats["max_refresh_time"], refresh_time)
        stats["min_refresh_time"] = min(stats["min_refresh_time"], refresh_time)

    def stop(self):
        """停止监控仪表板"""
        self.is_running = False
        logger.info("🛑 正在停止监控仪表板...")


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="四号引擎监控仪表板")
    parser.add_argument("--interval", type=float, default=1.0, help="刷新间隔（秒）")
    parser.add_argument("--no-prometheus", action="store_true", help="禁用Prometheus")
    parser.add_argument("--no-alerts", action="store_true", help="禁用告警")
    parser.add_argument("--alert-config", type=str, help="告警配置文件路径")
    parser.add_argument("--port", type=int, default=9091, help="Prometheus端口")
    parser.add_argument("--simple", action="store_true", help="使用简单文本输出")

    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()

    # 配置
    config = DashboardConfig(
        refresh_interval=args.interval,
        enable_prometheus=not args.no_prometheus,
        prometheus_port=args.port,
        enable_alerts=not args.no_alerts,
        alert_config_path=args.alert_config
    )

    # 如果指定了简单输出，禁用Rich
    global RICH_AVAILABLE
    if args.simple:
        RICH_AVAILABLE = False

    # 创建并运行仪表板
    dashboard = MonitoringDashboard(config)

    try:
        # 运行仪表板
        asyncio.run(dashboard.run())
    except KeyboardInterrupt:
        print("\\n👋 监控仪表板已停止")


if __name__ == "__main__":
    main()
