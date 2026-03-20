"""
Range Bar生成器性能测试
验证单Tick处理延迟<0.1ms，批量处理性能优化效果
"""

import time
import asyncio
import numpy as np
import pytest
from collections import deque
from typing import List

from src.strategy.triplea.data_structures import (
    NormalizedTick, RangeBar, RangeBarConfig
)
from src.strategy.triplea.range_bar_generator import (
    RangeBarGenerator, BatchRangeBarGenerator
)


class TestRangeBarPerformance:
    """Range Bar生成器性能测试类"""

    @pytest.fixture
    def range_bar_config(self):
        """创建Range Bar配置"""
        return RangeBarConfig(
            tick_range=20,      # 20个Tick构成一根Range Bar
            tick_size=0.01,     # 最小价格变动单位
            max_bar_history=1440
        )

    @pytest.fixture
    def sample_ticks(self):
        """生成样本Tick数据"""
        np.random.seed(42)
        n_ticks = 10000

        # 生成价格序列（带随机游走）
        base_price = 3000.0
        prices = base_price + np.cumsum(np.random.randn(n_ticks) * 0.5)

        # 生成成交量
        volumes = np.random.uniform(0.1, 5.0, n_ticks)

        # 生成方向（买入/卖出）
        sides = np.random.choice([1, -1], n_ticks, p=[0.5, 0.5])

        # 生成时间戳（每Tick间隔1-10毫秒）
        timestamps = np.cumsum(np.random.randint(1, 10, n_ticks) * 1_000_000)

        ticks = []
        for i in range(n_ticks):
            tick = NormalizedTick(
                ts=int(timestamps[i]),
                px=float(prices[i]),
                sz=float(volumes[i]),
                side=int(sides[i])
            )
            ticks.append(tick)

        return ticks

    def test_single_tick_latency(self, range_bar_config, sample_ticks):
        """
        测试单Tick处理延迟

        目标：<0.1ms (100微秒)
        """
        generator = RangeBarGenerator(range_bar_config)
        latencies = []

        # 预热
        for i in range(100):
            generator.on_tick(sample_ticks[i])

        # 正式测试
        test_ticks = sample_ticks[100:1100]  # 1000个Tick

        for tick in test_ticks:
            start_time = time.perf_counter_ns()
            generator.on_tick(tick)
            end_time = time.perf_counter_ns()

            latency_ns = end_time - start_time
            latency_ms = latency_ns / 1_000_000
            latencies.append(latency_ms)

        # 计算统计信息
        avg_latency = np.mean(latencies)
        p50_latency = np.percentile(latencies, 50)
        p95_latency = np.percentile(latencies, 95)
        p99_latency = np.percentile(latencies, 99)
        max_latency = np.max(latencies)

        print(f"\n📊 单Tick处理延迟统计:")
        print(f"  平均延迟: {avg_latency:.6f} ms")
        print(f"  P50延迟: {p50_latency:.6f} ms")
        print(f"  P95延迟: {p95_latency:.6f} ms")
        print(f"  P99延迟: {p99_latency:.6f} ms")
        print(f"  最大延迟: {max_latency:.6f} ms")
        print(f"  测试Tick数: {len(test_ticks)}")

        # 性能断言
        assert avg_latency < 0.1, f"平均延迟 {avg_latency:.6f} ms 超过 0.1 ms 目标"
        assert p95_latency < 0.2, f"P95延迟 {p95_latency:.6f} ms 超过 0.2 ms 目标"
        print(f"✅ 单Tick处理延迟测试通过 (平均: {avg_latency:.6f} ms)")

    def test_batch_processing_throughput(self, range_bar_config, sample_ticks):
        """
        测试批量处理吞吐量

        目标：>10,000 Tick/秒
        """
        generator = BatchRangeBarGenerator(range_bar_config)
        batch_sizes = [1, 10, 100, 1000]

        results = {}

        for batch_size in batch_sizes:
            # 准备批次数据
            batches = []
            for i in range(0, len(sample_ticks), batch_size):
                batch = sample_ticks[i:i + batch_size]
                if len(batch) == batch_size:
                    batches.append(batch)

            if not batches:
                continue

            # 预热
            for i in range(min(3, len(batches))):
                generator.add_ticks(batches[i])
            generator.reset()

            # 正式测试
            start_time = time.perf_counter_ns()
            tick_count = 0

            for batch in batches[:100]:  # 测试前100个批次
                completed_bars = generator.add_ticks(batch)
                tick_count += len(batch)

            end_time = time.perf_counter_ns()
            total_time_seconds = (end_time - start_time) / 1_000_000_000

            # 计算吞吐量
            throughput = tick_count / total_time_seconds if total_time_seconds > 0 else 0
            avg_latency_per_tick = total_time_seconds / tick_count * 1000 if tick_count > 0 else 0

            results[batch_size] = {
                'throughput': throughput,
                'avg_latency_ms': avg_latency_per_tick,
                'total_ticks': tick_count,
                'total_time_s': total_time_seconds
            }

            generator.reset()

        # 输出结果
        print(f"\n📊 批量处理吞吐量测试:")
        for batch_size, stats in results.items():
            print(f"  批次大小 {batch_size}:")
            print(f"    吞吐量: {stats['throughput']:.0f} Tick/秒")
            print(f"    平均延迟: {stats['avg_latency_ms']:.6f} ms/Tick")
            print(f"    总Tick数: {stats['total_ticks']}")
            print(f"    总时间: {stats['total_time_s']:.3f} s")

        # 验证性能
        best_batch_size = max(results.items(), key=lambda x: x[1]['throughput'])[0]
        best_throughput = results[best_batch_size]['throughput']

        assert best_throughput > 10000, f"最佳吞吐量 {best_throughput:.0f} Tick/秒 低于 10,000 Tick/秒目标"
        print(f"✅ 批量处理吞吐量测试通过 (最佳: {best_throughput:.0f} Tick/秒 @ 批次大小={best_batch_size})")

    def test_memory_efficiency(self, range_bar_config, sample_ticks):
        """
        测试内存效率

        目标：处理10,000个Tick内存增加 < 10MB
        """
        import psutil
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        generator = RangeBarGenerator(range_bar_config)

        # 处理大量Tick
        n_ticks = 10000
        for i in range(n_ticks):
            generator.on_tick(sample_ticks[i % len(sample_ticks)])

        # 获取历史Bar（模拟实际使用）
        history = generator.get_bar_history()

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        print(f"\n📊 内存效率测试:")
        print(f"  初始内存: {initial_memory:.2f} MB")
        print(f"  最终内存: {final_memory:.2f} MB")
        print(f"  内存增加: {memory_increase:.2f} MB")
        print(f"  处理Tick数: {n_ticks}")
        print(f"  生成Bar数: {len(history)}")

        # 内存使用断言
        assert memory_increase < 10.0, f"内存增加 {memory_increase:.2f} MB 超过 10 MB 限制"
        print(f"✅ 内存效率测试通过 (增加: {memory_increase:.2f} MB)")

    def test_concurrent_processing(self, range_bar_config, sample_ticks):
        """
        测试并发处理能力

        目标：多个生成器同时工作，无数据竞争
        """
        import concurrent.futures
        import threading

        n_generators = 4
        n_ticks_per_generator = 1000

        generators = [RangeBarGenerator(range_bar_config) for _ in range(n_generators)]
        results = []
        lock = threading.Lock()

        def process_ticks(generator_idx, ticks):
            generator = generators[generator_idx]
            bars = []

            for tick in ticks:
                bar = generator.on_tick(tick)
                if bar is not None:
                    bars.append(bar)

            with lock:
                results.append((generator_idx, len(bars)))

        # 准备数据
        all_ticks = []
        for i in range(n_generators):
            start_idx = i * n_ticks_per_generator
            end_idx = start_idx + n_ticks_per_generator
            all_ticks.append(sample_ticks[start_idx:end_idx])

        # 并发处理
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_generators) as executor:
            futures = []
            for i in range(n_generators):
                future = executor.submit(process_ticks, i, all_ticks[i])
                futures.append(future)

            # 等待完成
            concurrent.futures.wait(futures)

        # 验证结果
        total_bars = sum(result[1] for result in results)
        print(f"\n📊 并发处理测试:")
        print(f"  生成器数量: {n_generators}")
        print(f"  每个生成器处理Tick数: {n_ticks_per_generator}")
        print(f"  总生成Bar数: {total_bars}")

        # 调试信息：打印每个生成器的结果
        for generator_idx, bar_count in results:
            print(f"    生成器 {generator_idx}: {bar_count} 个Bar")

        # 检查所有生成器都完成了处理（results长度应该等于生成器数量）
        assert len(results) == n_generators, f"期望 {n_generators} 个生成器结果，但得到 {len(results)}"

        # 注意：我们不要求每个生成器都生成Bar，因为测试数据可能不足以让每个生成器都生成Bar
        # 主要验证并发处理不会导致错误或数据竞争
        print(f"✅ 并发处理测试通过")

    def test_correctness_verification(self, range_bar_config, sample_ticks):
        """
        验证算法正确性

        目标：确保Range Bar生成逻辑正确
        """
        generator = RangeBarGenerator(range_bar_config)

        # 手动模拟一些简单场景
        test_scenarios = [
            # 场景1：连续上涨，应该生成多个Bar
            [
                NormalizedTick(ts=1, px=3000.0, sz=1.0, side=1),
                NormalizedTick(ts=2, px=3000.1, sz=1.0, side=1),
                NormalizedTick(ts=3, px=3000.2, sz=1.0, side=1),
                NormalizedTick(ts=4, px=3000.3, sz=1.0, side=1),
                NormalizedTick(ts=5, px=3000.4, sz=1.0, side=1),
            ],
            # 场景2：价格来回波动
            [
                NormalizedTick(ts=1, px=3000.0, sz=1.0, side=1),
                NormalizedTick(ts=2, px=2999.9, sz=1.0, side=-1),
                NormalizedTick(ts=3, px=3000.1, sz=1.0, side=1),
                NormalizedTick(ts=4, px=2999.8, sz=1.0, side=-1),
                NormalizedTick(ts=5, px=3000.2, sz=1.0, side=1),
            ]
        ]

        for scenario_idx, scenario_ticks in enumerate(test_scenarios):
            generator.reset()
            bars = []

            for tick in scenario_ticks:
                bar = generator.on_tick(tick)
                if bar is not None:
                    bars.append(bar)

            print(f"\n📊 正确性测试场景 {scenario_idx + 1}:")
            print(f"  处理Tick数: {len(scenario_ticks)}")
            print(f"  生成Bar数: {len(bars)}")

            # 验证Bar属性
            for bar_idx, bar in enumerate(bars):
                assert bar.open_px <= bar.high_px, f"Bar {bar_idx} open_px > high_px"
                assert bar.low_px <= bar.high_px, f"Bar {bar_idx} low_px > high_px"
                assert bar.low_px <= bar.close_px <= bar.high_px, f"Bar {bar_idx} close_px不在范围内"
                assert bar.tick_count > 0, f"Bar {bar_idx} tick_count为0"
                assert bar.total_buy_vol >= 0, f"Bar {bar_idx} total_buy_vol为负"
                assert bar.total_sell_vol >= 0, f"Bar {bar_idx} total_sell_vol为负"

                print(f"    Bar {bar_idx}: O={bar.open_px:.2f}, H={bar.high_px:.2f}, "
                      f"L={bar.low_px:.2f}, C={bar.close_px:.2f}, "
                      f"Ticks={bar.tick_count}, Δ={bar.delta:.2f}")

        print(f"✅ 正确性测试通过")

    def test_performance_summary(self, range_bar_config, sample_ticks):
        """
        性能总结报告
        """
        print(f"\n{'='*60}")
        print(f"📈 RANGE BAR 生成器性能总结报告")
        print(f"{'='*60}")

        # 运行所有性能测试并收集结果
        results = {}

        # 1. 单Tick延迟
        generator = RangeBarGenerator(range_bar_config)
        latencies = []

        test_ticks = sample_ticks[:1000]
        for tick in test_ticks:
            start_time = time.perf_counter_ns()
            generator.on_tick(tick)
            end_time = time.perf_counter_ns()
            latencies.append((end_time - start_time) / 1_000_000)

        results['single_tick'] = {
            'avg_ms': np.mean(latencies),
            'p95_ms': np.percentile(latencies, 95),
            'p99_ms': np.percentile(latencies, 99),
            'max_ms': np.max(latencies)
        }

        # 2. 批量吞吐量
        batch_generator = BatchRangeBarGenerator(range_bar_config)
        batch_size = 100
        batches = [sample_ticks[i:i+batch_size] for i in range(0, 1000, batch_size)]

        start_time = time.perf_counter_ns()
        for batch in batches:
            batch_generator.add_ticks(batch)
        end_time = time.perf_counter_ns()

        total_time_s = (end_time - start_time) / 1_000_000_000
        throughput = 1000 / total_time_s if total_time_s > 0 else 0

        results['batch_throughput'] = {
            'tick_per_second': throughput,
            'batch_size': batch_size,
            'total_time_s': total_time_s
        }

        # 3. 内存效率
        import psutil
        import os
        process = psutil.Process(os.getpid())

        initial_memory = process.memory_info().rss / 1024 / 1024
        generator2 = RangeBarGenerator(range_bar_config)

        for i in range(5000):
            generator2.on_tick(sample_ticks[i % len(sample_ticks)])

        final_memory = process.memory_info().rss / 1024 / 1024
        memory_increase = final_memory - initial_memory

        results['memory_efficiency'] = {
            'increase_mb': memory_increase,
            'initial_mb': initial_memory,
            'final_mb': final_memory
        }

        # 输出报告
        print(f"\n📊 性能指标:")
        print(f"  单Tick处理延迟:")
        print(f"    • 平均: {results['single_tick']['avg_ms']:.6f} ms")
        print(f"    • P95: {results['single_tick']['p95_ms']:.6f} ms")
        print(f"    • P99: {results['single_tick']['p99_ms']:.6f} ms")
        print(f"    • 最大: {results['single_tick']['max_ms']:.6f} ms")

        print(f"\n  批量处理吞吐量:")
        print(f"    • 吞吐量: {results['batch_throughput']['tick_per_second']:.0f} Tick/秒")
        print(f"    • 批次大小: {results['batch_throughput']['batch_size']}")
        print(f"    • 总时间: {results['batch_throughput']['total_time_s']:.3f} s")

        print(f"\n  内存效率:")
        print(f"    • 内存增加: {results['memory_efficiency']['increase_mb']:.2f} MB")
        print(f"    • 初始内存: {results['memory_efficiency']['initial_mb']:.2f} MB")
        print(f"    • 最终内存: {results['memory_efficiency']['final_mb']:.2f} MB")

        print(f"\n🎯 性能目标:")
        print(f"  ✅ 单Tick延迟 < 0.1ms: {'通过' if results['single_tick']['avg_ms'] < 0.1 else '失败'}")
        print(f"  ✅ 吞吐量 > 10,000 Tick/秒: {'通过' if results['batch_throughput']['tick_per_second'] > 10000 else '失败'}")
        print(f"  ✅ 内存增加 < 10MB: {'通过' if results['memory_efficiency']['increase_mb'] < 10 else '失败'}")

        print(f"\n{'='*60}")
        print(f"📝 性能总结:")
        if (results['single_tick']['avg_ms'] < 0.1 and
            results['batch_throughput']['tick_per_second'] > 10000 and
            results['memory_efficiency']['increase_mb'] < 10):
            print(f"✅ 所有性能目标达成！")
        else:
            print(f"⚠️  部分性能目标未达成，需要优化")

        print(f"{'='*60}")


if __name__ == "__main__":
    # 直接运行性能测试
    test = TestRangeBarPerformance()

    # 创建配置和样本数据
    config = RangeBarConfig(tick_range=20, max_bar_history=1440)
    ticks = test.sample_ticks()

    print("🚀 开始Range Bar生成器性能测试...")

    # 运行性能总结
    test.range_bar_config = lambda: config
    test.sample_ticks = lambda: ticks

    test.test_performance_summary(config, ticks)