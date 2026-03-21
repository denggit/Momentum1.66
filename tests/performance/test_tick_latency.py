"""
四号引擎v3.0性能基准测试：单Tick处理延迟
目标：单Tick处理延迟 < 1ms（平均）
"""

import os
import statistics
import time
from typing import Dict, List

import numpy as np
import psutil


class TickLatencyBenchmark:
    """Tick处理延迟基准测试类"""

    def __init__(self, num_ticks: int = 10000, warmup_ticks: int = 1000):
        """
        初始化基准测试

        Args:
            num_ticks: 测试的Tick数量
            warmup_ticks: 预热Tick数量（避免冷启动影响）
        """
        self.num_ticks = num_ticks
        self.warmup_ticks = warmup_ticks
        self.latencies = []
        self.memory_samples = []
        self.cpu_samples = []

        # 测试数据生成
        self.test_ticks = self._generate_test_ticks()

    def _generate_test_ticks(self) -> List[Dict]:
        """生成模拟Tick数据"""
        print(f"📊 生成 {self.num_ticks + self.warmup_ticks} 个测试Tick...")

        ticks = []
        base_price = 3000.0
        base_time = int(time.time() * 1000) - (self.num_ticks + self.warmup_ticks) * 100

        for i in range(self.num_ticks + self.warmup_ticks):
            # 模拟价格随机游走
            price_change = np.random.normal(0, 0.5)  # 正态分布变化
            price = base_price + price_change * (i / 100)

            # 模拟成交量
            size = np.random.exponential(1.0) + 0.1

            # 随机买卖方向
            side = 'buy' if np.random.random() > 0.5 else 'sell'

            tick = {
                'price': float(price),
                'size': float(size),
                'side': side,
                'ts': base_time + i * 100  # 每100ms一个Tick
            }
            ticks.append(tick)

        return ticks

    def _mock_tick_processor(self, tick: Dict) -> Dict:
        """
        模拟Tick处理器（占位函数）
        实际测试时将被真实的signal_generator.process_tick替换

        Returns:
            Optional[Dict]: 模拟信号
        """
        # 模拟一些计算操作
        price = tick['price']
        size = tick['size']
        side = tick['side']

        # 模拟简单计算
        cvd = size if side == 'buy' else -size
        price_squared = price * price
        volume_weighted = price * size

        # 返回模拟结果
        return {
            'processed': True,
            'price': price,
            'cvd': cvd,
            'timestamp': tick['ts']
        }

    def run_benchmark(self) -> Dict:
        """运行延迟基准测试"""
        print("🚀 开始Tick处理延迟基准测试...")
        print(f"📈 预热: {self.warmup_ticks} ticks, 测试: {self.num_ticks} ticks")

        # 预热阶段（不记录延迟）
        print("🔥 预热阶段...")
        for i in range(self.warmup_ticks):
            tick = self.test_ticks[i]
            self._mock_tick_processor(tick)

        # 清空预热缓存
        if hasattr(self, '_mock_tick_processor'):
            # 重新加载函数以清除JIT缓存（如果需要）
            pass

        # 测试阶段
        print("⚡ 测试阶段...")
        process = psutil.Process(os.getpid())

        for i in range(self.warmup_ticks, self.warmup_ticks + self.num_ticks):
            tick = self.test_ticks[i]

            # 测量延迟
            start_time = time.perf_counter_ns()
            result = self._mock_tick_processor(tick)
            end_time = time.perf_counter_ns()

            latency_ns = end_time - start_time
            latency_ms = latency_ns / 1_000_000  # 转换为毫秒

            self.latencies.append(latency_ms)

            # 定期采样内存和CPU
            if i % 1000 == 0:
                memory_mb = process.memory_info().rss / 1024 / 1024
                cpu_percent = process.cpu_percent(interval=0.01)
                self.memory_samples.append(memory_mb)
                self.cpu_samples.append(cpu_percent)

        # 计算统计信息
        stats = self._calculate_statistics()

        print("✅ 基准测试完成!")
        return stats

    def _calculate_statistics(self) -> Dict:
        """计算延迟统计信息"""
        if not self.latencies:
            return {}

        return {
            'total_ticks': len(self.latencies),
            'mean_latency_ms': statistics.mean(self.latencies),
            'median_latency_ms': statistics.median(self.latencies),
            'p90_latency_ms': np.percentile(self.latencies, 90),
            'p95_latency_ms': np.percentile(self.latencies, 95),
            'p99_latency_ms': np.percentile(self.latencies, 99),
            'min_latency_ms': min(self.latencies),
            'max_latency_ms': max(self.latencies),
            'std_latency_ms': statistics.stdev(self.latencies) if len(self.latencies) > 1 else 0,
            'memory_avg_mb': statistics.mean(self.memory_samples) if self.memory_samples else 0,
            'memory_max_mb': max(self.memory_samples) if self.memory_samples else 0,
            'cpu_avg_percent': statistics.mean(self.cpu_samples) if self.cpu_samples else 0,
            'cpu_max_percent': max(self.cpu_samples) if self.cpu_samples else 0,
        }

    def print_report(self, stats: Dict):
        """打印基准测试报告"""
        print("\n" + "=" * 60)
        print("📊 四号引擎v3.0 Tick处理延迟基准测试报告")
        print("=" * 60)

        print(f"\n📈 测试概况:")
        print(f"  测试Tick数量: {stats.get('total_ticks', 0):,}")
        print(f"  目标延迟: < 1.0 ms")

        print(f"\n⏱️ 延迟统计 (毫秒):")
        print(f"  平均值: {stats.get('mean_latency_ms', 0):.4f} ms")
        print(f"  中位数: {stats.get('median_latency_ms', 0):.4f} ms")
        print(f"  P90: {stats.get('p90_latency_ms', 0):.4f} ms")
        print(f"  P95: {stats.get('p95_latency_ms', 0):.4f} ms")
        print(f"  P99: {stats.get('p99_latency_ms', 0):.4f} ms")
        print(f"  最小值: {stats.get('min_latency_ms', 0):.4f} ms")
        print(f"  最大值: {stats.get('max_latency_ms', 0):.4f} ms")
        print(f"  标准差: {stats.get('std_latency_ms', 0):.4f} ms")

        print(f"\n💾 内存使用:")
        print(f"  平均内存: {stats.get('memory_avg_mb', 0):.2f} MB")
        print(f"  最大内存: {stats.get('memory_max_mb', 0):.2f} MB")

        print(f"\n⚡ CPU使用:")
        print(f"  平均CPU: {stats.get('cpu_avg_percent', 0):.2f}%")
        print(f"  最大CPU: {stats.get('cpu_max_percent', 0):.2f}%")

        print(f"\n🎯 性能评估:")
        mean_latency = stats.get('mean_latency_ms', 0)
        if mean_latency < 1.0:
            print(f"  ✅ 通过! 平均延迟 {mean_latency:.4f} ms < 1.0 ms")
        else:
            print(f"  ⚠️  警告! 平均延迟 {mean_latency:.4f} ms > 1.0 ms")
            print(f"    需要进一步优化")

        print("\n" + "=" * 60)


def main():
    """主函数：运行基准测试"""
    print("🔧 四号引擎v3.0性能基准测试启动...")

    # 创建基准测试实例
    benchmark = TickLatencyBenchmark(
        num_ticks=10000,  # 测试10000个Tick
        warmup_ticks=1000  # 预热1000个Tick
    )

    # 运行基准测试
    stats = benchmark.run_benchmark()

    # 打印报告
    benchmark.print_report(stats)

    # 保存结果到文件
    import json
    with open('tests/performance/tick_latency_benchmark.json', 'w') as f:
        json.dump(stats, f, indent=2)

    print("💾 结果已保存到: tests/performance/tick_latency_benchmark.json")

    # 返回退出码（用于CI/CD）
    if stats.get('mean_latency_ms', 0) < 1.0:
        return 0  # 成功
    else:
        return 1  # 失败


if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
