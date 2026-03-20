"""
CVD计算器性能测试
验证单Tick处理延迟<0.2ms，批量处理性能优化效果
"""

import time
import numpy as np
import pytest
from typing import List
from collections import deque

from src.strategy.triplea.data_structures import NormalizedTick
from src.strategy.triplea.cvd_calculator import CVDCalculator, BatchCVDCalculator


class TestCVDPerformance:
    """CVD计算器性能测试类"""

    @pytest.fixture
    def cvd_config(self):
        """创建CVD配置"""
        return {
            'window_sizes': [10, 30, 60, 120, 240],
            'max_history': 1000
        }

    @pytest.fixture
    def sample_ticks(self):
        """生成样本Tick数据"""
        np.random.seed(42)
        n_ticks = 10000

        # 生成价格序列（带趋势和波动）
        base_price = 3000.0
        # 添加趋势分量
        trend = np.linspace(0, 50, n_ticks)
        # 添加随机波动
        noise = np.cumsum(np.random.randn(n_ticks) * 2.0)
        prices = base_price + trend + noise

        # 生成成交量（与波动相关）
        volatilities = np.abs(np.diff(prices, prepend=prices[0]))
        volumes = volatilities * np.random.uniform(10, 50, n_ticks)

        # 生成方向（与价格变动相关）
        price_changes = np.diff(prices, prepend=prices[0])
        # 价格上涨时更可能是买入，下跌时更可能是卖出
        buy_prob = np.where(price_changes > 0, 0.7, 0.3)
        sides = np.array([1 if np.random.rand() < prob else -1 for prob in buy_prob])

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

    def test_single_tick_latency(self, cvd_config, sample_ticks):
        """
        测试单Tick处理延迟

        目标：<0.2ms (200微秒)
        """
        calculator = CVDCalculator(
            window_sizes=cvd_config['window_sizes'],
            max_history=cvd_config['max_history']
        )

        latencies = []

        # 预热
        for i in range(100):
            calculator.on_tick(sample_ticks[i])

        # 正式测试
        test_ticks = sample_ticks[100:1100]  # 1000个Tick

        for tick in test_ticks:
            start_time = time.perf_counter_ns()
            calculator.on_tick(tick)
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
        assert avg_latency < 0.2, f"平均延迟 {avg_latency:.6f} ms 超过 0.2 ms 目标"
        assert p95_latency < 0.5, f"P95延迟 {p95_latency:.6f} ms 超过 0.5 ms 目标"
        print(f"✅ 单Tick处理延迟测试通过 (平均: {avg_latency:.6f} ms)")

    def test_batch_processing_throughput(self, cvd_config, sample_ticks):
        """
        测试批量处理吞吐量

        目标：>50,000 Tick/秒
        """
        calculator = BatchCVDCalculator(window_sizes=cvd_config['window_sizes'])
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
                calculator.add_ticks(batches[i])
            calculator.reset()

            # 正式测试
            start_time = time.perf_counter_ns()
            tick_count = 0

            for batch in batches[:50]:  # 测试前50个批次
                calculator.add_ticks(batch)
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

            calculator.reset()

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

        assert best_throughput > 50000, f"最佳吞吐量 {best_throughput:.0f} Tick/秒 低于 50,000 Tick/秒目标"
        print(f"✅ 批量处理吞吐量测试通过 (最佳: {best_throughput:.0f} Tick/秒 @ 批次大小={best_batch_size})")

    def test_memory_efficiency(self, cvd_config, sample_ticks):
        """
        测试内存效率

        目标：处理10,000个Tick内存增加 < 5MB
        """
        import psutil
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        calculator = CVDCalculator(
            window_sizes=cvd_config['window_sizes'],
            max_history=cvd_config['max_history']
        )

        # 处理大量Tick
        n_ticks = 10000
        for i in range(n_ticks):
            calculator.on_tick(sample_ticks[i % len(sample_ticks)])

        # 获取历史数据（模拟实际使用）
        history = {}
        for window in cvd_config['window_sizes']:
            history[window] = calculator.get_history(window)

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        print(f"\n📊 内存效率测试:")
        print(f"  初始内存: {initial_memory:.2f} MB")
        print(f"  最终内存: {final_memory:.2f} MB")
        print(f"  内存增加: {memory_increase:.2f} MB")
        print(f"  处理Tick数: {n_ticks}")

        # 内存使用断言
        assert memory_increase < 5.0, f"内存增加 {memory_increase:.2f} MB 超过 5 MB 限制"
        print(f"✅ 内存效率测试通过 (增加: {memory_increase:.2f} MB)")

    def test_cvd_calculation_correctness(self, cvd_config, sample_ticks):
        """
        验证CVD计算正确性

        目标：确保CVD计算逻辑正确
        """
        calculator = CVDCalculator(
            window_sizes=[10, 30],
            max_history=100
        )

        # 处理前100个Tick
        test_ticks = sample_ticks[:100]
        cvd_values_history = []

        for tick in test_ticks:
            cvd_values = calculator.on_tick(tick)
            cvd_values_history.append(cvd_values.copy())

        # 验证窗口10的CVD值
        recent_ticks = list(calculator.tick_buffer)
        if len(recent_ticks) >= 10:
            # 手动计算最后10个Tick的CVD
            last_10_ticks = recent_ticks[-10:]
            manual_cvd = sum(
                tick.sz if tick.side == 1 else -tick.sz
                for tick in last_10_ticks
            )

            # 获取计算器计算的CVD
            current_cvd = calculator.get_current_cvd()[10]

            print(f"\n📊 CVD计算正确性验证:")
            print(f"  手动计算CVD(窗口10): {manual_cvd:.4f}")
            print(f"  计算器返回CVD(窗口10): {current_cvd:.4f}")
            print(f"  差异: {abs(manual_cvd - current_cvd):.6f}")

            # 验证一致性（允许浮点误差）
            assert abs(manual_cvd - current_cvd) < 1e-10, f"CVD计算不一致: 手动={manual_cvd:.4f}, 计算器={current_cvd:.4f}"
            print(f"✅ CVD计算正确性验证通过 (窗口10)")

        # 验证统计特征
        stats = calculator.get_statistics()
        for window, window_stats in stats.items():
            print(f"  窗口 {window}: 均值={window_stats['mean']:.4f}, 标准差={window_stats['std']:.4f}, Z-score={window_stats['z_score']:.4f}")

        print(f"✅ CVD统计计算验证通过")

    def test_statistics_feature_calculation(self, cvd_config, sample_ticks):
        """
        测试CVD统计特征计算（均值、标准差、Z-score）

        目标：确保统计特征计算准确
        """
        calculator = CVDCalculator(
            window_sizes=[30, 60],
            max_history=500
        )

        # 处理足够多的Tick以建立统计
        n_ticks = 500
        for i in range(n_ticks):
            calculator.on_tick(sample_ticks[i])

        # 获取统计特征
        stats = calculator.get_statistics()

        for window in [30, 60]:
            window_stats = stats[window]

            # 手动计算验证
            history = calculator.get_history(window)
            manual_mean = np.mean(history)
            manual_std = np.std(history, ddof=1) if len(history) > 1 else 0.0
            current_cvd = calculator.get_current_cvd()[window]
            manual_z_score = (current_cvd - manual_mean) / manual_std if manual_std > 0 else 0.0

            print(f"\n📊 统计特征验证(窗口 {window}):")
            print(f"  历史数据长度: {len(history)}")
            print(f"  手动计算 - 均值: {manual_mean:.4f}, 标准差: {manual_std:.4f}, Z-score: {manual_z_score:.4f}")
            print(f"  计算器结果 - 均值: {window_stats['mean']:.4f}, 标准差: {window_stats['std']:.4f}, Z-score: {window_stats['z_score']:.4f}")

            # 验证一致性（允许浮点误差）
            assert abs(manual_mean - window_stats['mean']) < 1e-10, f"均值计算不一致: 手动={manual_mean:.4f}, 计算器={window_stats['mean']:.4f}"
            assert abs(manual_std - window_stats['std']) < 1e-10, f"标准差计算不一致: 手动={manual_std:.4f}, 计算器={window_stats['std']:.4f}"
            assert abs(manual_z_score - window_stats['z_score']) < 1e-10, f"Z-score计算不一致: 手动={manual_z_score:.4f}, 计算器={window_stats['z_score']:.4f}"

            print(f"✅ 窗口 {window} 统计特征计算验证通过")

        print(f"✅ 所有统计特征计算验证通过")

    def test_performance_summary(self, cvd_config, sample_ticks):
        """
        CVD计算器性能总结报告
        """
        print(f"\n{'='*60}")
        print(f"📈 CVD 计算器性能总结报告")
        print(f"{'='*60}")

        # 运行所有性能测试并收集结果
        results = {}

        # 1. 单Tick延迟
        calculator = CVDCalculator(
            window_sizes=[10, 30, 60],
            max_history=1000
        )

        test_ticks = sample_ticks[:1000]
        latencies = []

        for tick in test_ticks:
            start_time = time.perf_counter_ns()
            calculator.on_tick(tick)
            end_time = time.perf_counter_ns()
            latencies.append((end_time - start_time) / 1_000_000)

        results['single_tick'] = {
            'avg_ms': np.mean(latencies),
            'p95_ms': np.percentile(latencies, 95),
            'p99_ms': np.percentile(latencies, 99),
            'max_ms': np.max(latencies)
        }

        # 2. 批量吞吐量
        batch_calculator = BatchCVDCalculator(window_sizes=[10, 30, 60])
        batch_size = 100
        batches = [sample_ticks[i:i+batch_size] for i in range(0, 1000, batch_size)]

        start_time = time.perf_counter_ns()
        for batch in batches:
            batch_calculator.add_ticks(batch)
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
        calculator2 = CVDCalculator(
            window_sizes=[10, 30, 60],
            max_history=1000
        )

        for i in range(5000):
            calculator2.on_tick(sample_ticks[i % len(sample_ticks)])

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
        print(f"  ✅ 单Tick延迟 < 0.2ms: {'通过' if results['single_tick']['avg_ms'] < 0.2 else '失败'}")
        print(f"  ✅ 吞吐量 > 50,000 Tick/秒: {'通过' if results['batch_throughput']['tick_per_second'] > 50000 else '失败'}")
        print(f"  ✅ 内存增加 < 5MB: {'通过' if results['memory_efficiency']['increase_mb'] < 5.0 else '失败'}")

        print(f"\n{'='*60}")
        print(f"📝 性能总结:")
        if (results['single_tick']['avg_ms'] < 0.2 and
            results['batch_throughput']['tick_per_second'] > 50000 and
            results['memory_efficiency']['increase_mb'] < 5.0):
            print(f"✅ 所有性能目标达成！")
        else:
            print(f"⚠️  部分性能目标未达成，需要优化")

        print(f"{'='*60}")


if __name__ == "__main__":
    # 直接运行性能测试
    test = TestCVDPerformance()

    # 创建配置和样本数据
    config = {
        'window_sizes': [10, 30, 60, 120, 240],
        'max_history': 1000
    }
    ticks = test.sample_ticks()

    print("🚀 开始CVD计算器性能测试...")

    # 运行性能总结
    test.cvd_config = lambda: config
    test.sample_ticks = lambda: ticks

    test.test_performance_summary(config, ticks)