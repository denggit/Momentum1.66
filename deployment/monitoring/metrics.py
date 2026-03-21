#!/usr/bin/env python3
"""
四号引擎监控指标收集器
收集系统指标、性能指标和业务指标
"""

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Any

import psutil

# Prometheus客户端（可选）
try:
    from prometheus_client import Gauge, Counter, Histogram, start_http_server

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("⚠️  Prometheus客户端未安装，监控指标将仅记录到日志")


@dataclass
class SystemMetrics:
    """系统指标数据类"""
    timestamp: float
    cpu_percent: float  # CPU使用率百分比
    memory_percent: float  # 内存使用率百分比
    memory_used_mb: float  # 内存使用量(MB)
    disk_usage_percent: float  # 磁盘使用率百分比
    network_sent_mb: float  # 网络发送量(MB)
    network_recv_mb: float  # 网络接收量(MB)
    process_count: int  # 进程数量
    load_average: tuple  # 系统负载(1min, 5min, 15min)


@dataclass
class PerformanceMetrics:
    """性能指标数据类"""
    timestamp: float
    tick_latency_ms: float  # Tick处理延迟(毫秒)
    cvd_computation_ms: float  # CVD计算延迟(毫秒)
    kde_computation_ms: float  # KDE计算延迟(毫秒)
    state_transition_ms: float  # 状态转换延迟(毫秒)
    total_latency_ms: float  # 总处理延迟(毫秒)
    queue_depth: int  # 任务队列深度
    cache_hit_rate: float  # 缓存命中率


@dataclass
class BusinessMetrics:
    """业务指标数据类"""
    timestamp: float
    tick_count: int  # 处理的Tick数量
    order_count: int  # 订单数量
    signal_count: int  # 信号数量
    error_count: int  # 错误数量
    current_state: str  # 当前状态机状态
    position_pnl: float  # 持仓盈亏
    account_balance: float  # 账户余额
    risk_exposure: float  # 风险暴露


class MetricsCollector:
    """指标收集器"""

    def __init__(self, history_size: int = 1000):
        """
        初始化指标收集器

        Args:
            history_size: 历史记录保留数量
        """
        self.history_size = history_size

        # 指标历史记录
        self.system_metrics_history = deque(maxlen=history_size)
        self.performance_metrics_history = deque(maxlen=history_size)
        self.business_metrics_history = deque(maxlen=history_size)

        # 网络流量基准（用于计算增量）
        self.last_net_io = psutil.net_io_counters()
        self.last_net_time = time.time()

        # Prometheus指标（如果可用）
        if PROMETHEUS_AVAILABLE:
            self._init_prometheus_metrics()

    def _init_prometheus_metrics(self):
        """初始化Prometheus指标"""
        # 系统指标
        self.prom_cpu_percent = Gauge(
            'triplea_system_cpu_percent',
            'CPU使用率百分比',
            ['host']
        )

        self.prom_memory_used_mb = Gauge(
            'triplea_system_memory_used_mb',
            '内存使用量(MB)',
            ['host']
        )

        self.prom_disk_usage_percent = Gauge(
            'triplea_system_disk_usage_percent',
            '磁盘使用率百分比',
            ['host', 'mountpoint']
        )

        # 性能指标
        self.prom_tick_latency_ms = Histogram(
            'triplea_performance_tick_latency_ms',
            'Tick处理延迟(毫秒)',
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
        )

        self.prom_total_latency_ms = Histogram(
            'triplea_performance_total_latency_ms',
            '总处理延迟(毫秒)',
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
        )

        # 业务指标
        self.prom_tick_count = Counter(
            'triplea_business_tick_count',
            '处理的Tick数量'
        )

        self.prom_signal_count = Counter(
            'triplea_business_signal_count',
            '生成的信号数量',
            ['signal_type']
        )

        self.prom_error_count = Counter(
            'triplea_business_error_count',
            '错误数量',
            ['error_type']
        )

    def collect_system_metrics(self) -> SystemMetrics:
        """
        收集系统指标

        Returns:
            SystemMetrics: 系统指标数据
        """
        # CPU使用率
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # 内存使用
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_mb = memory.used / 1024 / 1024

        # 磁盘使用
        disk = psutil.disk_usage('/')
        disk_usage_percent = disk.percent

        # 网络流量（计算增量）
        current_time = time.time()
        current_net_io = psutil.net_io_counters()

        time_diff = current_time - self.last_net_time
        if time_diff > 0:
            sent_mb = (current_net_io.bytes_sent - self.last_net_io.bytes_sent) / 1024 / 1024
            recv_mb = (current_net_io.bytes_recv - self.last_net_io.bytes_recv) / 1024 / 1024
        else:
            sent_mb = recv_mb = 0

        # 更新基准
        self.last_net_io = current_net_io
        self.last_net_time = current_time

        # 进程数量
        process_count = len(list(psutil.process_iter()))

        # 系统负载
        load_avg = psutil.getloadavg()

        # 创建指标对象
        metrics = SystemMetrics(
            timestamp=time.time(),
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            memory_used_mb=memory_used_mb,
            disk_usage_percent=disk_usage_percent,
            network_sent_mb=sent_mb,
            network_recv_mb=recv_mb,
            process_count=process_count,
            load_average=load_avg
        )

        # 保存到历史记录
        self.system_metrics_history.append(metrics)

        # 更新Prometheus指标
        if PROMETHEUS_AVAILABLE:
            self.prom_cpu_percent.labels(host='localhost').set(cpu_percent)
            self.prom_memory_used_mb.labels(host='localhost').set(memory_used_mb)
            self.prom_disk_usage_percent.labels(host='localhost', mountpoint='/').set(disk_usage_percent)

        return metrics

    def collect_performance_metrics(
            self,
            tick_latency_ms: float = 0,
            cvd_computation_ms: float = 0,
            kde_computation_ms: float = 0,
            state_transition_ms: float = 0,
            queue_depth: int = 0,
            cache_hit_rate: float = 0
    ) -> PerformanceMetrics:
        """
        收集性能指标

        Args:
            tick_latency_ms: Tick处理延迟(毫秒)
            cvd_computation_ms: CVD计算延迟(毫秒)
            kde_computation_ms: KDE计算延迟(毫秒)
            state_transition_ms: 状态转换延迟(毫秒)
            queue_depth: 任务队列深度
            cache_hit_rate: 缓存命中率

        Returns:
            PerformanceMetrics: 性能指标数据
        """
        # 计算总延迟
        total_latency_ms = tick_latency_ms + cvd_computation_ms + kde_computation_ms + state_transition_ms

        # 创建指标对象
        metrics = PerformanceMetrics(
            timestamp=time.time(),
            tick_latency_ms=tick_latency_ms,
            cvd_computation_ms=cvd_computation_ms,
            kde_computation_ms=kde_computation_ms,
            state_transition_ms=state_transition_ms,
            total_latency_ms=total_latency_ms,
            queue_depth=queue_depth,
            cache_hit_rate=cache_hit_rate
        )

        # 保存到历史记录
        self.performance_metrics_history.append(metrics)

        # 更新Prometheus指标
        if PROMETHEUS_AVAILABLE:
            self.prom_tick_latency_ms.observe(tick_latency_ms)
            self.prom_total_latency_ms.observe(total_latency_ms)

        return metrics

    def collect_business_metrics(
            self,
            tick_count: int = 0,
            order_count: int = 0,
            signal_count: int = 0,
            error_count: int = 0,
            current_state: str = "IDLE",
            position_pnl: float = 0,
            account_balance: float = 0,
            risk_exposure: float = 0
    ) -> BusinessMetrics:
        """
        收集业务指标

        Args:
            tick_count: 处理的Tick数量
            order_count: 订单数量
            signal_count: 信号数量
            error_count: 错误数量
            current_state: 当前状态机状态
            position_pnl: 持仓盈亏
            account_balance: 账户余额
            risk_exposure: 风险暴露

        Returns:
            BusinessMetrics: 业务指标数据
        """
        # 创建指标对象
        metrics = BusinessMetrics(
            timestamp=time.time(),
            tick_count=tick_count,
            order_count=order_count,
            signal_count=signal_count,
            error_count=error_count,
            current_state=current_state,
            position_pnl=position_pnl,
            account_balance=account_balance,
            risk_exposure=risk_exposure
        )

        # 保存到历史记录
        self.business_metrics_history.append(metrics)

        # 更新Prometheus指标
        if PROMETHEUS_AVAILABLE:
            self.prom_tick_count.inc(tick_count)
            # 信号和错误计数需要根据具体类型更新

        return metrics

    def get_recent_system_metrics(self, count: int = 100) -> List[SystemMetrics]:
        """
        获取最近的系统指标

        Args:
            count: 获取数量

        Returns:
            List[SystemMetrics]: 系统指标列表
        """
        return list(self.system_metrics_history)[-count:]

    def get_recent_performance_metrics(self, count: int = 100) -> List[PerformanceMetrics]:
        """
        获取最近的性能指标

        Args:
            count: 获取数量

        Returns:
            List[PerformanceMetrics]: 性能指标列表
        """
        return list(self.performance_metrics_history)[-count:]

    def get_recent_business_metrics(self, count: int = 100) -> List[BusinessMetrics]:
        """
        获取最近的业务指标

        Args:
            count: 获取数量

        Returns:
            List[BusinessMetrics]: 业务指标列表
        """
        return list(self.business_metrics_history)[-count:]

    def get_system_summary(self) -> Dict[str, Any]:
        """
        获取系统指标摘要

        Returns:
            Dict[str, Any]: 系统指标摘要
        """
        if not self.system_metrics_history:
            return {}

        recent_metrics = self.get_recent_system_metrics(10)
        if not recent_metrics:
            return {}

        latest = recent_metrics[-1]

        return {
            "timestamp": datetime.fromtimestamp(latest.timestamp).isoformat(),
            "cpu_percent": round(latest.cpu_percent, 2),
            "memory_used_mb": round(latest.memory_used_mb, 2),
            "disk_usage_percent": round(latest.disk_usage_percent, 2),
            "load_average": latest.load_average,
            "process_count": latest.process_count
        }

    def get_performance_summary(self) -> Dict[str, Any]:
        """
        获取性能指标摘要

        Returns:
            Dict[str, Any]: 性能指标摘要
        """
        if not self.performance_metrics_history:
            return {}

        recent_metrics = self.get_recent_performance_metrics(100)
        if not recent_metrics:
            return {}

        # 计算统计信息
        latencies = [m.total_latency_ms for m in recent_metrics]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        max_latency = max(latencies) if latencies else 0
        min_latency = min(latencies) if latencies else 0

        cache_hit_rates = [m.cache_hit_rate for m in recent_metrics if m.cache_hit_rate > 0]
        avg_cache_hit_rate = sum(cache_hit_rates) / len(cache_hit_rates) if cache_hit_rates else 0

        return {
            "avg_total_latency_ms": round(avg_latency, 4),
            "max_total_latency_ms": round(max_latency, 4),
            "min_total_latency_ms": round(min_latency, 4),
            "avg_cache_hit_rate": round(avg_cache_hit_rate, 2),
            "sample_count": len(recent_metrics)
        }

    def get_business_summary(self) -> Dict[str, Any]:
        """
        获取业务指标摘要

        Returns:
            Dict[str, Any]: 业务指标摘要
        """
        if not self.business_metrics_history:
            return {}

        recent_metrics = self.get_recent_business_metrics(100)
        if not recent_metrics:
            return {}

        latest = recent_metrics[-1]

        # 计算统计信息
        tick_counts = [m.tick_count for m in recent_metrics]
        total_ticks = sum(tick_counts) if tick_counts else 0

        signal_counts = [m.signal_count for m in recent_metrics]
        total_signals = sum(signal_counts) if signal_counts else 0

        error_counts = [m.error_count for m in recent_metrics]
        total_errors = sum(error_counts) if error_counts else 0

        return {
            "current_state": latest.current_state,
            "total_ticks": total_ticks,
            "total_signals": total_signals,
            "total_errors": total_errors,
            "position_pnl": round(latest.position_pnl, 2),
            "account_balance": round(latest.account_balance, 2),
            "risk_exposure": round(latest.risk_exposure, 2),
            "sample_count": len(recent_metrics)
        }

    def start_prometheus_server(self, port: int = 9091):
        """
        启动Prometheus指标服务器

        Args:
            port: 服务器端口
        """
        if not PROMETHEUS_AVAILABLE:
            print("⚠️  Prometheus客户端未安装，无法启动指标服务器")
            return

        start_http_server(port)
        print(f"✅ Prometheus指标服务器启动在端口 {port}")


async def metrics_collection_task(collector: MetricsCollector, interval: float = 1.0):
    """
    指标收集任务（异步）

    Args:
        collector: 指标收集器实例
        interval: 收集间隔（秒）
    """
    while True:
        try:
            # 收集系统指标
            collector.collect_system_metrics()

            # 等待下一个收集周期
            await asyncio.sleep(interval)

        except Exception as e:
            print(f"❌ 指标收集任务出错: {e}")
            await asyncio.sleep(interval)


def main():
    """测试主函数"""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

    # 创建指标收集器
    collector = MetricsCollector()

    # 启动Prometheus服务器（如果可用）
    if PROMETHEUS_AVAILABLE:
        collector.start_prometheus_server(port=9091)

    # 测试收集指标
    print("🧪 测试指标收集器...")

    # 收集系统指标
    system_metrics = collector.collect_system_metrics()
    print(f"✅ 系统指标收集成功:")
    print(f"   - CPU使用率: {system_metrics.cpu_percent:.1f}%")
    print(f"   - 内存使用: {system_metrics.memory_used_mb:.1f} MB")
    print(f"   - 磁盘使用: {system_metrics.disk_usage_percent:.1f}%")

    # 收集性能指标
    perf_metrics = collector.collect_performance_metrics(
        tick_latency_ms=0.5,
        cvd_computation_ms=0.2,
        kde_computation_ms=1.0,
        state_transition_ms=0.1,
        queue_depth=5,
        cache_hit_rate=0.95
    )
    print(f"✅ 性能指标收集成功:")
    print(f"   - 总延迟: {perf_metrics.total_latency_ms:.3f} ms")
    print(f"   - 缓存命中率: {perf_metrics.cache_hit_rate:.1%}")

    # 收集业务指标
    biz_metrics = collector.collect_business_metrics(
        tick_count=1000,
        order_count=5,
        signal_count=3,
        error_count=0,
        current_state="MONITORING",
        position_pnl=15.5,
        account_balance=300.0,
        risk_exposure=0.05
    )
    print(f"✅ 业务指标收集成功:")
    print(f"   - 当前状态: {biz_metrics.current_state}")
    print(f"   - 持仓盈亏: {biz_metrics.position_pnl:.2f} USDT")
    print(f"   - 风险暴露: {biz_metrics.risk_exposure:.1%}")

    # 获取摘要
    system_summary = collector.get_system_summary()
    perf_summary = collector.get_performance_summary()
    biz_summary = collector.get_business_summary()

    print(f"\\n📊 系统摘要: {json.dumps(system_summary, indent=2)}")
    print(f"📊 性能摘要: {json.dumps(perf_summary, indent=2)}")
    print(f"📊 业务摘要: {json.dumps(biz_summary, indent=2)}")

    print("\\n✅ 指标收集器测试完成")


if __name__ == "__main__":
    main()
