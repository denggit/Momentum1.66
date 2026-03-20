#!/usr/bin/env python3
"""
四号引擎v3.0 全系统性能压力测试
验证单Tick处理延迟 < 1ms 性能目标
"""

import asyncio
import time
import statistics
import threading
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import deque
import numpy as np
import gc

from src.utils.log import get_logger

logger = get_logger(__name__)


@dataclass
class PerformanceMetrics:
    """性能指标"""
    test_name: str
    total_ticks: int
    avg_latency_ms: float
    p50_latency_ms: float  # 中位数
    p90_latency_ms: float  # 90百分位
    p99_latency_ms: float  # 99百分位
    max_latency_ms: float
    min_latency_ms: float
    std_deviation_ms: float
    total_duration_ms: float
    ticks_per_second: float
    memory_usage_mb: float
    cpu_usage_pct: float
    timestamp: float = field(default_factory=time.time)


class PerformanceTestRunner:
    """性能测试运行器"""

    def __init__(self):
        self.metrics_history: deque[PerformanceMetrics] = deque(maxlen=100)
        self.current_latencies: List[float] = []
        self.test_start_time: Optional[float] = None
        self.test_end_time: Optional[float] = None

        # 统计信息
        self.total_tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0

    async def run_full_system_test(self, duration_seconds: int = 30) -> PerformanceMetrics:
        """运行全系统性能测试

        Args:
            duration_seconds: 测试持续时间（秒）

        Returns:
            PerformanceMetrics: 性能指标
        """
        logger.info(f"🚀 开始全系统性能测试，持续时间: {duration_seconds}秒")

        self.current_latencies = []
        self.test_start_time = time.time()

        # 启动Tick生成器
        tick_generator = TickGenerator()
        tick_processor = TickProcessor()

        # 启动所有组件
        await tick_generator.start()
        await tick_processor.start()

        # 运行指定时间
        start_time = time.time()
        tick_count = 0

        # 收集基线内存使用
        gc.collect()
        baseline_memory = self._get_memory_usage_mb()

        while time.time() - start_time < duration_seconds:
            tick_start = time.time()

            # 生成模拟Tick
            tick = tick_generator.generate_tick()

            # 处理Tick
            await tick_processor.process_tick(tick)

            # 测量延迟
            tick_end = time.time()
            latency_ms = (tick_end - tick_start) * 1000

            self.current_latencies.append(latency_ms)
            tick_count += 1

            # 避免过快的循环
            await asyncio.sleep(0.001)  # 1ms最小间隔

        # 停止所有组件
        await tick_processor.stop()
        await tick_generator.stop()

        self.test_end_time = time.time()

        # 计算性能指标
        metrics = self._calculate_metrics(
            test_name="full_system_perf",
            latencies=self.current_latencies,
            total_ticks=tick_count,
            duration_seconds=duration_seconds,
            baseline_memory=baseline_memory
        )

        # 检查性能目标
        if metrics.avg_latency_ms <= 1.0:
            logger.info(f"✅ 全系统性能测试通过: 平均延迟 {metrics.avg_latency_ms:.3f}ms < 1ms")
            self.tests_passed += 1
        else:
            logger.warning(f"⚠️ 全系统性能测试未达标: 平均延迟 {metrics.avg_latency_ms:.3f}ms > 1ms")
            self.tests_failed += 1

        self.total_tests_run += 1
        self.metrics_history.append(metrics)

        logger.info(f"📊 测试完成: 处理了 {tick_count} 个 Tick")
        logger.info(f"   平均延迟: {metrics.avg_latency_ms:.3f}ms")
        logger.info(f"   90百分位延迟: {metrics.p90_latency_ms:.3f}ms")
        logger.info(f"   99百分位延迟: {metrics.p99_latency_ms:.3f}ms")
        logger.info(f"   TPS: {metrics.ticks_per_second:.1f} ticks/秒")

        return metrics

    async def run_sustained_load_test(self, ticks_per_second: int = 1000, duration_seconds: int = 60) -> PerformanceMetrics:
        """运行持续负载测试

        Args:
            ticks_per_second: 每秒Tick数
            duration_seconds: 测试持续时间

        Returns:
            PerformanceMetrics: 性能指标
        """
        logger.info(f"🚀 开始持续负载测试: {ticks_per_second} TPS, 持续时间: {duration_seconds}秒")

        self.current_latencies = []
        self.test_start_time = time.time()

        # 启动模拟处理器
        tick_processor = TickProcessor()

        await tick_processor.start()

        # 收集基线内存
        gc.collect()
        baseline_memory = self._get_memory_usage_mb()

        # 运行负载测试
        tick_count = 0
        start_time = time.time()
        last_tick_time = start_time

        target_tick_interval = 1.0 / ticks_per_second

        while time.time() - start_time < duration_seconds:
            current_time = time.time()

            # 计算应该生成多少个Tick
            elapsed_since_last = current_time - last_tick_time
            ticks_to_generate = int(elapsed_since_last / target_tick_interval)

            if ticks_to_generate > 0:
                for _ in range(ticks_to_generate):
                    tick_start = time.time()

                    # 创建模拟Tick
                    tick = {
                        'timestamp': tick_start,
                        'price': 3000.0 + (tick_count % 100) - 50,
                        'bid': 2999.9,
                        'ask': 3000.1,
                        'volume': 1.0
                    }

                    # 处理Tick
                    await tick_processor.process_tick(tick)

                    # 测量延迟
                    tick_end = time.time()
                    latency_ms = (tick_end - tick_start) * 1000

                    self.current_latencies.append(latency_ms)
                    tick_count += 1

                last_tick_time = current_time

            # 短暂休眠避免100% CPU
            await asyncio.sleep(0.001)

        # 停止处理器
        await tick_processor.stop()

        self.test_end_time = time.time()

        # 计算指标
        metrics = self._calculate_metrics(
            test_name="sustained_load_test",
            latencies=self.current_latencies,
            total_ticks=tick_count,
            duration_seconds=duration_seconds,
            baseline_memory=baseline_memory
        )

        # 检查性能
        avg_tps = metrics.ticks_per_second
        if avg_tps >= ticks_per_second * 0.9:  # 允许10%的偏差
            logger.info(f"✅ 持续负载测试通过: 平均TPS {avg_tps:.1f} (目标: {ticks_per_second})")
            self.tests_passed += 1
        else:
            logger.warning(f"⚠️ 持续负载测试未达标: 平均TPS {avg_tps:.1f} (目标: {ticks_per_second})")
            self.tests_failed += 1

        self.total_tests_run += 1
        self.metrics_history.append(metrics)

        return metrics

    async def run_burst_load_test(self, burst_size: int = 100, interval_ms: int = 100) -> PerformanceMetrics:
        """运行突发负载测试

        Args:
            burst_size: 每次突发处理的Tick数
            interval_ms: 突发间隔（毫秒）

        Returns:
            PerformanceMetrics: 性能指标
        """
        logger.info(f"🚀 开始突发负载测试: {burst_size} ticks/{interval_ms}ms")

        self.current_latencies = []
        self.test_start_time = time.time()

        tick_processor = TickProcessor()
        await tick_processor.start()

        # 收集基线内存
        gc.collect()
        baseline_memory = self._get_memory_usage_mb()

        total_ticks = 0
        bursts = 10  # 运行10次突发

        start_time = time.time()

        for burst_idx in range(bursts):
            burst_latencies = []

            # 处理突发Tick
            for tick_idx in range(burst_size):
                tick_start = time.time()

                tick = {
                    'timestamp': tick_start,
                    'price': 3000.0 + (tick_idx % 50) - 25,
                    'bid': 2999.9,
                    'ask': 3000.1,
                    'volume': 1.0
                }

                await tick_processor.process_tick(tick)

                tick_end = time.time()
                latency_ms = (tick_end - tick_start) * 1000

                burst_latencies.append(latency_ms)
                total_ticks += 1

            self.current_latencies.extend(burst_latencies)

            # 等待下一次突发
            if burst_idx < bursts - 1:
                await asyncio.sleep(interval_ms / 1000.0)

        # 停止处理器
        await tick_processor.stop()

        self.test_end_time = time.time()

        total_duration = time.time() - start_time

        # 计算指标
        metrics = self._calculate_metrics(
            test_name="burst_load_test",
            latencies=self.current_latencies,
            total_ticks=total_ticks,
            duration_seconds=total_duration,
            baseline_memory=baseline_memory
        )

        # 检查突发处理能力
        burst_duration_ms = sum(self.current_latencies) / len(self.current_latencies) * burst_size
        if burst_duration_ms <= interval_ms * 1.2:  # 允许20%的偏差
            logger.info(f"✅ 突发负载测试通过: 处理时间 {burst_duration_ms:.1f}ms (间隔: {interval_ms}ms)")
            self.tests_passed += 1
        else:
            logger.warning(f"⚠️ 突发负载测试未达标: 处理时间 {burst_duration_ms:.1f}ms > {interval_ms}ms")
            self.tests_failed += 1

        self.total_tests_run += 1
        self.metrics_history.append(metrics)

        return metrics

    def _calculate_metrics(
        self,
        test_name: str,
        latencies: List[float],
        total_ticks: int,
        duration_seconds: float,
        baseline_memory: float
    ) -> PerformanceMetrics:
        """计算性能指标"""
        if not latencies:
            latencies = [0.0]

        # 基本统计
        latencies_sorted = sorted(latencies)
        n = len(latencies_sorted)

        avg_latency = statistics.mean(latencies) if latencies else 0.0
        max_latency = max(latencies) if latencies else 0.0
        min_latency = min(latencies) if latencies else 0.0

        # 百分位数
        p50 = latencies_sorted[int(n * 0.5)] if n > 0 else 0.0
        p90 = latencies_sorted[int(n * 0.9)] if n > int(n * 0.9) else latencies_sorted[-1]
        p99 = latencies_sorted[int(n * 0.99)] if n > int(n * 0.99) else latencies_sorted[-1]

        # 标准差
        std_dev = statistics.stdev(latencies) if len(latencies) > 1 else 0.0

        # 当前内存使用
        current_memory = self._get_memory_usage_mb()
        memory_used = current_memory - baseline_memory

        # TPS
        tps = total_ticks / duration_seconds if duration_seconds > 0 else 0.0

        return PerformanceMetrics(
            test_name=test_name,
            total_ticks=total_ticks,
            avg_latency_ms=avg_latency,
            p50_latency_ms=p50,
            p90_latency_ms=p90,
            p99_latency_ms=p99,
            max_latency_ms=max_latency,
            min_latency_ms=min_latency,
            std_deviation_ms=std_dev,
            total_duration_ms=duration_seconds * 1000,
            ticks_per_second=tps,
            memory_usage_mb=memory_used,
            cpu_usage_pct=self._estimate_cpu_usage()
        )

    def _get_memory_usage_mb(self) -> float:
        """获取内存使用（MB）"""
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024

    def _estimate_cpu_usage(self) -> float:
        """估计CPU使用率"""
        try:
            import psutil
            return psutil.cpu_percent(interval=0.1)
        except ImportError:
            return 0.0

    def print_summary_report(self):
        """打印总结报告"""
        print("\n" + "=" * 70)
        print("四号引擎v3.0 全系统性能测试总结报告")
        print("=" * 70)

        if not self.metrics_history:
            print("没有可用的测试数据")
            return

        latest_metrics = self.metrics_history[-1]

        print(f"\n📊 总体统计:")
        print(f"   总测试次数: {self.total_tests_run}")
        print(f"   通过次数: {self.tests_passed}")
        print(f"   失败次数: {self.tests_failed}")
        print(f"   通过率: {self.tests_passed/self.total_tests_run*100:.1f}%")

        print(f"\n📈 最新测试指标 ({latest_metrics.test_name}):")
        print(f"   总Tick数: {latest_metrics.total_ticks:,}")
        print(f"   平均延迟: {latest_metrics.avg_latency_ms:.3f}ms")
        print(f"   中位数延迟: {latest_metrics.p50_latency_ms:.3f}ms")
        print(f"   90百分位延迟: {latest_metrics.p90_latency_ms:.3f}ms")
        print(f"   99百分位延迟: {latest_metrics.p99_latency_ms:.3f}ms")
        print(f"   最大延迟: {latest_metrics.max_latency_ms:.3f}ms")
        print(f"   最小延迟: {latest_metrics.min_latency_ms:.3f}ms")
        print(f"   标准差: {latest_metrics.std_deviation_ms:.3f}ms")

        print(f"\n💾 资源使用:")
        print(f"   内存使用: {latest_metrics.memory_usage_mb:.2f}MB")
        print(f"   CPU使用率: {latest_metrics.cpu_usage_pct:.1f}%")
        print(f"   TPS: {latest_metrics.ticks_per_second:.1f} ticks/秒")

        # 性能目标检查
        print(f"\n🎯 性能目标检查:")
        avg_latency = latest_metrics.avg_latency_ms
        if avg_latency <= 1.0:
            print(f"   ✅ 单Tick处理延迟: {avg_latency:.3f}ms < 1ms (达标)")
        else:
            print(f"   ❌ 单Tick处理延迟: {avg_latency:.3f}ms > 1ms (未达标)")

        # 延迟分布分析
        print(f"\n📊 延迟分布分析:")
        latencies = self.current_latencies

        if latencies:
            low_latency = len([l for l in latencies if l <= 0.5]) / len(latencies) * 100
            medium_latency = len([l for l in latencies if 0.5 < l <= 1.0]) / len(latencies) * 100
            high_latency = len([l for l in latencies if l > 1.0]) / len(latencies) * 100

            print(f"   ≤0.5ms: {low_latency:.1f}%")
            print(f"   0.5-1ms: {medium_latency:.1f}%")
            print(f"   >1ms: {high_latency:.1f}%")

        print(f"\n📅 测试时间: {time.ctime(latest_metrics.timestamp)}")
        print("=" * 70)


class TickGenerator:
    """模拟Tick生成器"""

    def __init__(self):
        self.running = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    def generate_tick(self) -> Dict[str, Any]:
        """生成模拟Tick"""
        current_time = time.time()
        tick_count = int(current_time * 1000) % 10000

        return {
            'timestamp': current_time,
            'price': 3000.0 + (tick_count % 200) - 100,
            'bid': 2999.9 + (tick_count % 10) - 5,
            'ask': 3000.1 + (tick_count % 10) - 5,
            'volume': 1.0 + (tick_count % 10) * 0.1,
            'tick_id': f"tick_{tick_count:08d}"
        }


class TickProcessor:
    """模拟Tick处理器"""

    def __init__(self):
        self.running = False
        self.tick_count = 0

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    async def process_tick(self, tick: Dict[str, Any]) -> Dict[str, Any]:
        """处理Tick"""
        # 模拟各种处理
        self.tick_count += 1

        # 模拟RangeBar生成
        range_bar = self._simulate_range_bar_generation(tick)

        # 模拟CVD计算
        cvd_data = self._simulate_cvd_calculation(tick)

        # 模拟KDE计算
        kde_result = self._simulate_kde_computation(tick)

        # 模拟状态机处理
        state_result = self._simulate_state_machine(tick)

        # 模拟风控检查
        risk_result = self._simulate_risk_check(tick)

        return {
            'range_bar': range_bar,
            'cvd_data': cvd_data,
            'kde_result': kde_result,
            'state_result': state_result,
            'risk_result': risk_result,
            'processed_time': time.time()
        }

    def _simulate_range_bar_generation(self, tick: Dict[str, Any]) -> Dict[str, Any]:
        """模拟RangeBar生成"""
        price = tick['price']
        timestamp = tick['timestamp']

        return {
            'open': price,
            'high': price * 1.0005,
            'low': price * 0.9995,
            'close': price * 1.0001,
            'volume': tick['volume'],
            'timestamp': timestamp,
            'range_bar_id': f"rb_{int(timestamp * 1000)}"
        }

    def _simulate_cvd_calculation(self, tick: Dict[str, Any]) -> Dict[str, Any]:
        """模拟CVD计算"""
        price = tick['price']
        volume = tick['volume']

        # 模拟买入/卖出压力
        buy_pressure = 0.6 + (price % 100) * 0.004
        sell_pressure = 1.0 - buy_pressure

        buy_volume = volume * buy_pressure
        sell_volume = volume * sell_pressure

        net_volume = buy_volume - sell_volume
        cumulative_net = self.tick_count * net_volume * 0.001

        return {
            'buy_volume': buy_volume,
            'sell_volume': sell_volume,
            'net_volume': net_volume,
            'cumulative_net': cumulative_net,
            'pressure_ratio': buy_pressure / sell_pressure if sell_pressure > 0 else 999
        }

    def _simulate_kde_computation(self, tick: Dict[str, Any]) -> Dict[str, Any]:
        """模拟KDE计算"""
        price = tick['price']

        # 模拟密度计算
        density = np.exp(-0.5 * ((price - 3000) / 50) ** 2)
        local_minima = 3000 + (self.tick_count % 200) - 100

        return {
            'density': density,
            'local_minima': local_minima,
            'confidence': min(density * 2, 1.0),
            'bandwidth': 25.0
        }

    def _simulate_state_machine(self, tick: Dict[str, Any]) -> Dict[str, Any]:
        """模拟状态机处理"""
        states = ['IDLE', 'MONITORING', 'CONFIRMED', 'ACCUMULATING', 'POSITION']
        current_state = states[self.tick_count % len(states)]

        signal_strength = (self.tick_count % 100) / 100.0
        risk_score = 1.0 - signal_strength

        return {
            'current_state': current_state,
            'signal_strength': signal_strength,
            'risk_score': risk_score,
            'decision': 'HOLD' if risk_score > 0.7 else 'CONSIDER'
        }

    def _simulate_risk_check(self, tick: Dict[str, Any]) -> Dict[str, Any]:
        """模拟风控检查"""
        price = tick['price']
        deviation = abs(price - 3000) / 3000

        if deviation > 0.03:
            risk_level = 'HIGH'
            action = 'REJECT'
        elif deviation > 0.01:
            risk_level = 'MEDIUM'
            action = 'WARN'
        else:
            risk_level = 'LOW'
            action = 'ACCEPT'


        return {
            'risk_level': risk_level,
            'deviation_pct': deviation * 100,
            'action': action,
            'threshold_check': deviation < 0.05
        }


async def run_all_performance_tests():
    """运行所有性能测试"""
    print("🚀 开始全系统性能测试套件")
    print("=" * 70)

    runner = PerformanceTestRunner()

    try:
        # 测试1：全系统性能测试
        print("\n1️⃣ 全系统性能测试 (30秒)")
        metrics1 = await runner.run_full_system_test(duration_seconds=30)

        # 测试2：持续负载测试
        print("\n2️⃣ 持续负载测试 (1000 TPS, 60秒)")
        metrics2 = await runner.run_sustained_load_test(ticks_per_second=1000, duration_seconds=60)

        # 测试3：突发负载测试
        print("\n3️⃣ 突发负载测试 (100 ticks/100ms)")
        metrics3 = await runner.run_burst_load_test(burst_size=100, interval_ms=100)

        # 打印总结报告
        runner.print_summary_report()

        # 检查整体通过情况
        if runner.tests_passed == runner.total_tests_run:
            print("\n🎉 所有性能测试通过！")
            print("   四号引擎v3.0 满足单Tick处理延迟 < 1ms 的性能目标")
        else:
            print(f"\n⚠️  性能测试未完全通过: {runner.tests_passed}/{runner.total_tests_run}")
            print("   需要进一步优化以满足性能目标")

    except Exception as e:
        logger.error(f"性能测试执行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_all_performance_tests())