#!/usr/bin/env python3
"""
四号引擎科考船监控仪表板
实时监控测试环境状态、性能指标和系统健康度
"""

import sys
import os
import time
import json
import psutil
import yaml
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import deque
import threading

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.utils.log import get_logger

logger = get_logger(__name__)


@dataclass
class SystemMetrics:
    """系统指标数据类"""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    disk_usage_percent: float
    network_sent_mb: float
    network_recv_mb: float
    process_count: int


@dataclass
class EngineMetrics:
    """四号引擎指标数据类"""
    timestamp: float
    tick_count: int = 0
    order_count: int = 0
    signal_count: int = 0
    error_count: int = 0
    current_state: str = "IDLE"
    position_pnl: float = 0.0
    daily_pnl: float = 0.0
    tick_processing_latency_ms: float = 0.0
    order_execution_latency_ms: float = 0.0


@dataclass
class TradingMetrics:
    """交易指标数据类"""
    timestamp: float
    symbol: str = ""
    price: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    volume_24h: float = 0.0
    position_size: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class MetricsCollector:
    """指标收集器"""

    def __init__(self, max_history: int = 1000):
        self.max_history = max_history

        # 指标历史
        self.system_history = deque(maxlen=max_history)
        self.engine_history = deque(maxlen=max_history)
        self.trading_history = deque(maxlen=max_history)

        # 当前指标
        self.current_system = None
        self.current_engine = None
        self.current_trading = None

        # 统计信息
        self.start_time = time.time()
        self.total_ticks = 0
        self.total_orders = 0
        self.total_signals = 0
        self.total_errors = 0

        # 配置
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """加载配置"""
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    return yaml.safe_load(f)
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
        return {}

    def collect_system_metrics(self) -> SystemMetrics:
        """收集系统指标"""
        try:
            # CPU使用率
            cpu_percent = psutil.cpu_percent(interval=0.1)

            # 内存使用
            memory = psutil.virtual_memory()

            # 磁盘使用
            disk = psutil.disk_usage('/')

            # 网络IO
            net_io = psutil.net_io_counters()

            metrics = SystemMetrics(
                timestamp=time.time(),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_used_mb=memory.used / 1024 / 1024,
                disk_usage_percent=disk.percent,
                network_sent_mb=net_io.bytes_sent / 1024 / 1024,
                network_recv_mb=net_io.bytes_recv / 1024 / 1024,
                process_count=len(psutil.pids())
            )

            self.current_system = metrics
            self.system_history.append(metrics)
            return metrics

        except Exception as e:
            logger.error(f"收集系统指标失败: {e}")
            return None

    def collect_engine_metrics(self, **kwargs) -> EngineMetrics:
        """收集引擎指标"""
        try:
            metrics = EngineMetrics(
                timestamp=time.time(),
                **{k: v for k, v in kwargs.items() if k in EngineMetrics.__annotations__}
            )

            # 更新统计
            self.total_ticks += metrics.tick_count
            self.total_orders += metrics.order_count
            self.total_signals += metrics.signal_count
            self.total_errors += metrics.error_count

            self.current_engine = metrics
            self.engine_history.append(metrics)
            return metrics

        except Exception as e:
            logger.error(f"收集引擎指标失败: {e}")
            return None

    def collect_trading_metrics(self, **kwargs) -> TradingMetrics:
        """收集交易指标"""
        try:
            metrics = TradingMetrics(
                timestamp=time.time(),
                **{k: v for k, v in kwargs.items() if k in TradingMetrics.__annotations__}
            )

            self.current_trading = metrics
            self.trading_history.append(metrics)
            return metrics

        except Exception as e:
            logger.error(f"收集交易指标失败: {e}")
            return None

    def get_performance_summary(self) -> Dict:
        """获取性能摘要"""
        if not self.engine_history:
            return {}

        latencies = [m.tick_processing_latency_ms for m in self.engine_history if m.tick_processing_latency_ms > 0]
        order_latencies = [m.order_execution_latency_ms for m in self.engine_history if m.order_execution_latency_ms > 0]

        return {
            "total_ticks": self.total_ticks,
            "total_orders": self.total_orders,
            "total_signals": self.total_signals,
            "total_errors": self.total_errors,
            "avg_tick_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
            "avg_order_latency_ms": sum(order_latencies) / len(order_latencies) if order_latencies else 0,
            "uptime_seconds": time.time() - self.start_time,
            "success_rate": (self.total_ticks - self.total_errors) / self.total_ticks if self.total_ticks > 0 else 1.0
        }


class DashboardRenderer:
    """仪表板渲染器"""

    def __init__(self, collector: MetricsCollector):
        self.collector = collector
        self.console_width = 80

    def clear_screen(self):
        """清屏"""
        print("\033[2J\033[H", end="")

    def render_header(self):
        """渲染头部"""
        uptime = time.time() - self.collector.start_time
        uptime_str = str(timedelta(seconds=int(uptime)))

        print("=" * self.console_width)
        print("🚢 四号引擎科考船监控仪表板".center(self.console_width))
        print(f"运行时间: {uptime_str} | 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".center(self.console_width))
        print("=" * self.console_width)

    def render_system_status(self):
        """渲染系统状态"""
        print("\n📊 系统状态")
        print("-" * 40)

        if self.collector.current_system:
            sys = self.collector.current_system

            # CPU使用率
            cpu_bar = self._create_bar(sys.cpu_percent, 100)
            print(f"CPU使用率: {sys.cpu_percent:6.1f}% {cpu_bar}")

            # 内存使用
            mem_bar = self._create_bar(sys.memory_percent, 100)
            print(f"内存使用: {sys.memory_used_mb:6.1f}MB ({sys.memory_percent:5.1f}%) {mem_bar}")

            # 磁盘使用
            disk_bar = self._create_bar(sys.disk_usage_percent, 100)
            print(f"磁盘使用: {sys.disk_usage_percent:6.1f}% {disk_bar}")

            # 网络
            print(f"网络: ↑{sys.network_sent_mb:.1f}MB ↓{sys.network_recv_mb:.1f}MB")
            print(f"进程数: {sys.process_count}")
        else:
            print("系统指标不可用")

    def render_engine_status(self):
        """渲染引擎状态"""
        print("\n⚙️  四号引擎状态")
        print("-" * 40)

        if self.collector.current_engine:
            eng = self.collector.current_engine

            # 状态
            state_colors = {
                "IDLE": "🟢",
                "MONITORING": "🟡",
                "CONFIRMED": "🟠",
                "ACCUMULATING": "🔵",
                "POSITION": "🟣",
                "LONG": "🟢",
                "SHORT": "🔴"
            }
            state_icon = state_colors.get(eng.current_state, "⚪")
            print(f"状态: {state_icon} {eng.current_state}")

            # 性能指标
            perf = self.collector.get_performance_summary()
            print(f"Tick处理: {perf.get('total_ticks', 0):,} (延迟: {perf.get('avg_tick_latency_ms', 0):.3f}ms)")
            print(f"订单执行: {perf.get('total_orders', 0):,} (延迟: {perf.get('avg_order_latency_ms', 0):.1f}ms)")
            print(f"信号生成: {perf.get('total_signals', 0):,}")
            print(f"错误次数: {perf.get('total_errors', 0):,} (成功率: {perf.get('success_rate', 1.0)*100:.1f}%)")

            # P&L
            print(f"持仓盈亏: {eng.position_pnl:+.2f}U")
            print(f"当日盈亏: {eng.daily_pnl:+.2f}U")
        else:
            print("引擎指标不可用")

    def render_trading_status(self):
        """渲染交易状态"""
        print("\n💰 交易状态")
        print("-" * 40)

        if self.collector.current_trading:
            trading = self.collector.current_trading

            print(f"交易对: {trading.symbol}")
            print(f"最新价格: {trading.price:.2f}")
            print(f"买一价: {trading.bid_price:.2f} | 卖一价: {trading.ask_price:.2f}")
            print(f"24h成交量: {trading.volume_24h:,.0f}")

            if trading.position_size != 0:
                pos_color = "🟢" if trading.position_size > 0 else "🔴"
                print(f"持仓大小: {pos_color} {trading.position_size:.4f}")
                print(f"未实现盈亏: {trading.unrealized_pnl:+.2f}U")
                print(f"已实现盈亏: {trading.realized_pnl:+.2f}U")
            else:
                print("持仓: 无")
        else:
            print("交易指标不可用")

    def render_config_info(self):
        """渲染配置信息"""
        print("\n🔧 配置信息")
        print("-" * 40)

        config = self.collector.config

        env = config.get('environment', {})
        trading = config.get('trading', {})
        engine = config.get('triplea_engine', {})
        risk = engine.get('risk_management', {})

        print(f"测试模式: {env.get('mode', 'simulation')}")
        print(f"交易对: {trading.get('symbol', 'ETH-USDT-SWAP')}")
        print(f"杠杆: {trading.get('leverage', 3)}x")
        print(f"账户规模: {risk.get('account_size_usdt', 300.0)}U")
        print(f"单笔风险: {risk.get('max_risk_per_trade_pct', 5.0)}%")
        print(f"止损: {risk.get('stop_loss_ticks', 2)} ticks")
        print(f"止盈: {risk.get('take_profit_ticks', 6)} ticks")

    def render_alerts(self):
        """渲染告警信息"""
        print("\n⚠️  系统告警")
        print("-" * 40)

        alerts = []

        # 检查系统指标
        if self.collector.current_system:
            sys = self.collector.current_system

            if sys.cpu_percent > 80:
                alerts.append(f"CPU使用率过高: {sys.cpu_percent:.1f}%")

            if sys.memory_percent > 85:
                alerts.append(f"内存使用率过高: {sys.memory_percent:.1f}%")

            if sys.disk_usage_percent > 90:
                alerts.append(f"磁盘使用率过高: {sys.disk_usage_percent:.1f}%")

        # 检查引擎指标
        perf = self.collector.get_performance_summary()
        if perf.get('avg_tick_latency_ms', 0) > 10:
            alerts.append(f"Tick处理延迟过高: {perf['avg_tick_latency_ms']:.1f}ms")

        if perf.get('success_rate', 1.0) < 0.95:
            alerts.append(f"成功率过低: {perf['success_rate']*100:.1f}%")

        # 显示告警
        if alerts:
            for i, alert in enumerate(alerts[:5], 1):
                print(f"{i}. {alert}")

            if len(alerts) > 5:
                print(f"... 还有 {len(alerts) - 5} 个告警")
        else:
            print("✅ 系统运行正常")

    def render_footer(self):
        """渲染页脚"""
        print("\n" + "=" * self.console_width)
        print("💡 操作提示:".center(self.console_width))
        print("• Ctrl+C 退出监控 • R 刷新配置 • L 查看日志".center(self.console_width))
        print("=" * self.console_width)

    def _create_bar(self, value: float, max_value: float, width: int = 20) -> str:
        """创建进度条"""
        filled = int(value / max_value * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"

    def render(self):
        """渲染完整仪表板"""
        self.clear_screen()
        self.render_header()
        self.render_system_status()
        self.render_engine_status()
        self.render_trading_status()
        self.render_config_info()
        self.render_alerts()
        self.render_footer()


class MonitoringDashboard:
    """监控仪表板主类"""

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(os.path.dirname(__file__), "config.yaml")
        self.collector = MetricsCollector()
        self.renderer = DashboardRenderer(self.collector)
        self.running = False
        self.refresh_interval = 2.0  # 刷新间隔(秒)

        # 模拟数据线程
        self.simulation_thread = None

    def start_simulation(self):
        """启动模拟数据生成（用于测试）"""
        def simulate_data():
            tick_count = 0
            while self.running:
                time.sleep(0.1)

                # 模拟引擎数据
                self.collector.collect_engine_metrics(
                    tick_count=tick_count % 10,
                    order_count=tick_count % 100,
                    signal_count=tick_count % 1000,
                    error_count=tick_count % 10000,
                    current_state=["IDLE", "MONITORING", "CONFIRMED", "ACCUMULATING", "POSITION"][tick_count % 5],
                    position_pnl=(tick_count % 100) - 50,
                    daily_pnl=(tick_count % 1000) - 500,
                    tick_processing_latency_ms=0.5 + (tick_count % 100) / 100,
                    order_execution_latency_ms=50 + (tick_count % 1000) / 10
                )

                # 模拟交易数据
                if tick_count % 5 == 0:
                    self.collector.collect_trading_metrics(
                        symbol="ETH-USDT-SWAP",
                        price=3000 + (tick_count % 100) - 50,
                        bid_price=2999 + (tick_count % 100) - 50,
                        ask_price=3001 + (tick_count % 100) - 50,
                        volume_24h=1000000 + (tick_count % 100000),
                        position_size=0.1 if tick_count % 20 < 10 else -0.1,
                        unrealized_pnl=(tick_count % 100) - 50,
                        realized_pnl=(tick_count % 1000) - 500
                    )

                tick_count += 1

        self.simulation_thread = threading.Thread(target=simulate_data, daemon=True)
        self.simulation_thread.start()

    async def run(self):
        """运行监控仪表板"""
        self.running = True

        # 启动模拟数据（测试用）
        self.start_simulation()

        print("🚀 启动科考船监控仪表板...")
        print("📡 正在收集系统指标...")

        try:
            while self.running:
                # 收集系统指标
                self.collector.collect_system_metrics()

                # 渲染仪表板
                self.renderer.render()

                # 等待刷新间隔
                await asyncio.sleep(self.refresh_interval)

        except KeyboardInterrupt:
            print("\n\n🛑 收到停止信号，正在关闭监控仪表板...")
        except Exception as e:
            logger.error(f"监控仪表板运行错误: {e}", exc_info=True)
        finally:
            self.running = False
            print("✅ 监控仪表板已停止")

    def stop(self):
        """停止监控仪表板"""
        self.running = False


def main():
    """主函数"""
    # 检查是否在科考船目录
    current_dir = os.path.dirname(__file__)
    if not os.path.exists(os.path.join(current_dir, "config.yaml")):
        print("❌ 错误: 请在科考船目录运行此脚本")
        print(f"当前目录: {current_dir}")
        sys.exit(1)

    # 创建仪表板实例
    dashboard = MonitoringDashboard()

    # 运行仪表板
    try:
        asyncio.run(dashboard.run())
    except KeyboardInterrupt:
        print("\n👋 监控仪表板已手动停止")
    except Exception as e:
        print(f"\n❌ 监控仪表板运行失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()