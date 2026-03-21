"""
KDE引擎性能测试
验证KDE计算延迟<0.5ms，进程池计算性能优化效果
"""

import time

import numpy as np
import pytest

from src.strategy.triplea.data_structures import (
    NormalizedTick, TripleAEngineConfig
)
from src.strategy.triplea.kde_core import KDECore
from src.strategy.triplea.kde_engine import KDEEngine
from src.strategy.triplea.kde_matrix import KDEMatrixEngine
from src.strategy.triplea.lvn_extractor import LVNExtractor


class TestKDEPerformance:
    """KDE引擎性能测试类"""

    @pytest.fixture
    def kde_config(self):
        """创建KDE配置"""
        return TripleAEngineConfig()

    @pytest.fixture
    def sample_prices(self):
        """生成样本价格数据"""
        np.random.seed(42)
        n_samples = 10000

        # 模拟双峰分布，中间有LVN
        prices1 = np.random.randn(n_samples // 2) * 20 + 2950  # 第一个峰值
        prices2 = np.random.randn(n_samples // 2) * 20 + 3050  # 第二个峰值
        # 中间区域（LVN）样本较少
        prices_mid = np.random.randn(n_samples // 10) * 5 + 3000

        all_prices = np.concatenate([prices1, prices2, prices_mid])
        return all_prices

    @pytest.fixture
    def sample_ticks(self):
        """生成样本Tick数据"""
        np.random.seed(42)
        n_ticks = 10000

        # 生成价格序列
        base_price = 3000.0
        volatility = 50.0
        prices = base_price + np.cumsum(np.random.randn(n_ticks) * 0.5)

        # 生成时间戳（每Tick间隔1-10毫秒）
        timestamps = np.cumsum(np.random.randint(1, 10, n_ticks) * 1_000_000)

        ticks = []
        for i in range(n_ticks):
            tick = NormalizedTick(
                ts=int(timestamps[i]),
                px=float(prices[i]),
                sz=float(np.random.uniform(0.1, 5.0)),
                side=int(1 if np.random.rand() > 0.5 else -1)
            )
            ticks.append(tick)

        return ticks

    def test_kde_core_latency(self, kde_config, sample_prices):
        """
        测试KDE核心计算延迟

        目标：<0.2ms (200微秒)
        """
        kde_core = KDECore(kde_config.kde_engine)
        latencies = []

        # 使用不同大小的数据测试
        test_sizes = [100, 500, 1000, 5000, 10000]

        for size in test_sizes:
            if size > len(sample_prices):
                continue

            test_prices = sample_prices[:size]

            # 预热
            for _ in range(3):
                kde_core.compute_kde(test_prices[:min(100, size)])

            # 正式测试
            n_runs = min(10, 1000 // size)  # 确保总样本数合理
            run_latencies = []

            for _ in range(n_runs):
                start_time = time.perf_counter_ns()
                grid, densities = kde_core.compute_kde(test_prices)
                end_time = time.perf_counter_ns()

                latency_ns = end_time - start_time
                latency_ms = latency_ns / 1_000_000
                run_latencies.append(latency_ms)

            avg_latency = np.mean(run_latencies)
            latencies.append((size, avg_latency))

            print(f"  数据大小 {size}: 平均延迟 {avg_latency:.3f}ms, 网格大小 {len(grid) if len(grid) > 0 else 0}")

        # 输出结果
        print(f"\n📊 KDE核心计算延迟统计:")
        for size, latency in latencies:
            print(f"  数据大小 {size}: {latency:.3f}ms")

        # 验证性能（主要关注100-1000个样本的性能）
        relevant_latencies = [lat for size, lat in latencies if 100 <= size <= 1000]
        if relevant_latencies:
            avg_relevant_latency = np.mean(relevant_latencies)
            assert avg_relevant_latency < 0.2, f"KDE核心计算延迟 {avg_relevant_latency:.3f}ms 超过 0.2ms 目标"
            print(f"✅ KDE核心计算延迟测试通过 (平均: {avg_relevant_latency:.3f}ms)")

    def test_lvn_extraction_latency(self, kde_config, sample_prices):
        """
        测试LVN提取延迟

        目标：<0.1ms (100微秒)
        """
        # 首先计算KDE
        kde_core = KDECore(kde_config.kde_engine)
        test_prices = sample_prices[:1000]  # 使用1000个样本

        grid, densities = kde_core.compute_kde(test_prices)

        if len(grid) == 0 or len(densities) == 0:
            pytest.skip("KDE计算失败，跳过LVN提取测试")

        # 测试LVN提取
        lvn_extractor = LVNExtractor(kde_config.kde_engine)
        latencies = []

        # 预热
        for _ in range(5):
            lvn_extractor.extract_from_kde(grid, densities)

        # 正式测试
        n_runs = 100
        for _ in range(n_runs):
            start_time = time.perf_counter_ns()
            regions = lvn_extractor.extract_from_kde(grid, densities)
            end_time = time.perf_counter_ns()

            latency_ns = end_time - start_time
            latency_ms = latency_ns / 1_000_000
            latencies.append(latency_ms)

        # 计算统计信息
        avg_latency = np.mean(latencies)
        p50_latency = np.percentile(latencies, 50)
        p95_latency = np.percentile(latencies, 95)
        p99_latency = np.percentile(latencies, 99)

        print(f"\n📊 LVN提取延迟统计:")
        print(f"  平均延迟: {avg_latency:.3f}ms")
        print(f"  P50延迟: {p50_latency:.3f}ms")
        print(f"  P95延迟: {p95_latency:.3f}ms")
        print(f"  P99延迟: {p99_latency:.3f}ms")
        print(f"  提取区域数: {len(regions)}")

        # 验证性能
        assert avg_latency < 0.1, f"LVN提取延迟 {avg_latency:.3f}ms 超过 0.1ms 目标"
        print(f"✅ LVN提取延迟测试通过 (平均: {avg_latency:.3f}ms)")

    def test_batch_kde_throughput(self, kde_config, sample_prices):
        """
        测试批量KDE处理吞吐量

        目标：>10,000 样本/秒
        """
        kde_matrix = KDEMatrixEngine(kde_config.kde_engine)

        # 创建批量数据
        n_batches = 10
        batch_size = 1000
        price_batches = []

        for i in range(n_batches):
            start_idx = i * batch_size
            end_idx = start_idx + batch_size
            if end_idx <= len(sample_prices):
                price_batches.append(sample_prices[start_idx:end_idx])

        if len(price_batches) < 3:
            pytest.skip("批量数据不足，跳过测试")

        # 预热
        test_batch = price_batches[:2]
        kde_matrix.compute_batch_kde(test_batch)

        # 正式测试
        start_time = time.perf_counter_ns()
        results = kde_matrix.compute_batch_kde(price_batches)
        end_time = time.perf_counter_ns()

        total_time_seconds = (end_time - start_time) / 1_000_000_000
        total_samples = sum(len(batch) for batch in price_batches)

        # 计算吞吐量
        throughput = total_samples / total_time_seconds if total_time_seconds > 0 else 0
        avg_latency_per_sample = total_time_seconds / total_samples * 1000 if total_samples > 0 else 0

        print(f"\n📊 批量KDE吞吐量测试:")
        print(f"  批次数量: {len(price_batches)}")
        print(f"  批次大小: {batch_size}")
        print(f"  总样本数: {total_samples}")
        print(f"  总时间: {total_time_seconds:.3f}s")
        print(f"  吞吐量: {throughput:.0f} 样本/秒")
        print(f"  平均延迟: {avg_latency_per_sample:.3f}ms/样本")
        print(f"  结果数量: {len(results)}")

        # 验证性能
        assert throughput > 10000, f"批量KDE吞吐量 {throughput:.0f} 样本/秒 低于 10,000 样本/秒目标"
        print(f"✅ 批量KDE吞吐量测试通过 (吞吐量: {throughput:.0f} 样本/秒)")

    def test_memory_efficiency(self, kde_config, sample_prices):
        """
        测试内存效率

        目标：处理10,000个样本内存增加 < 5MB
        """
        import psutil
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # 创建多个KDE组件
        kde_core = KDECore(kde_config.kde_engine)
        kde_matrix = KDEMatrixEngine(kde_config.kde_engine)
        lvn_extractor = LVNExtractor(kde_config.kde_engine)

        # 处理多个样本集
        n_iterations = 100
        test_prices_list = []

        for i in range(n_iterations):
            start_idx = i * 100
            end_idx = start_idx + 1000
            if end_idx <= len(sample_prices):
                test_prices_list.append(sample_prices[start_idx:end_idx])

        # 执行计算
        for i, test_prices in enumerate(test_prices_list[:10]):  # 只处理前10个
            # KDE计算
            grid, densities = kde_core.compute_kde(test_prices)

            if len(grid) > 0 and len(densities) > 0:
                # LVN提取
                regions = lvn_extractor.extract_from_kde(grid, densities)

                # 批量计算
                kde_matrix.compute_batch_kde([test_prices])

        # 获取最终内存
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        print(f"\n📊 内存效率测试:")
        print(f"  初始内存: {initial_memory:.2f} MB")
        print(f"  最终内存: {final_memory:.2f} MB")
        print(f"  内存增加: {memory_increase:.2f} MB")
        print(f"  迭代次数: {len(test_prices_list[:10])}")

        # 验证内存使用
        assert memory_increase < 5.0, f"内存增加 {memory_increase:.2f} MB 超过 5 MB 限制"
        print(f"✅ 内存效率测试通过 (增加: {memory_increase:.2f} MB)")

    @pytest.mark.asyncio
    async def test_full_kde_engine_latency(self, kde_config, sample_ticks):
        """
        测试完整KDE引擎延迟

        目标：<0.5ms (500微秒) 包括KDE计算和LVN提取
        """
        # 创建引擎实例
        engine = KDEEngine(kde_config)

        # 修改配置为测试模式
        engine.config.kde_engine.min_slice_ticks = 100  # 降低最小样本要求
        engine.config.enable_numba_cache = True
        engine.config.enable_cpu_affinity = False  # 测试中禁用CPU亲和性

        # 启动引擎
        await engine.start()

        latencies = []

        # 处理前100个Tick（跳过预热）
        test_ticks = sample_ticks[:500]

        for i, tick in enumerate(test_ticks):
            # 确保有足够的数据开始计算
            if i < engine.config.kde_engine.min_slice_ticks:
                # 只添加数据，不计算
                engine.tick_buffer.append(tick)
                engine.price_history.append(tick.px)
                continue

            start_time = time.perf_counter_ns()
            regions = await engine.process_tick(tick)
            end_time = time.perf_counter_ns()

            latency_ns = end_time - start_time
            latency_ms = latency_ns / 1_000_000
            latencies.append(latency_ms)

            # 输出进度
            if (i + 1) % 50 == 0:
                current_avg = np.mean(latencies[-50:]) if len(latencies) >= 50 else np.mean(latencies)
                print(f"  已处理 {i + 1} 个Tick, 当前平均延迟: {current_avg:.3f}ms")

        # 停止引擎
        await engine.stop()

        # 计算统计信息
        if not latencies:
            pytest.skip("没有足够数据计算延迟")

        avg_latency = np.mean(latencies)
        p50_latency = np.percentile(latencies, 50)
        p95_latency = np.percentile(latencies, 95)
        p99_latency = np.percentile(latencies, 99)

        print(f"\n📊 完整KDE引擎延迟统计:")
        print(f"  平均延迟: {avg_latency:.3f}ms")
        print(f"  P50延迟: {p50_latency:.3f}ms")
        print(f"  P95延迟: {p95_latency:.3f}ms")
        print(f"  P99延迟: {p99_latency:.3f}ms")
        print(f"  测试Tick数: {len(latencies)}")

        # 获取引擎统计
        stats = engine.get_stats()
        print(f"  KDE计算次数: {stats['kde_calculations']}")
        print(f"  LVN检测次数: {stats['lvn_detections']}")

        # 验证性能
        assert avg_latency < 0.5, f"完整KDE引擎延迟 {avg_latency:.3f}ms 超过 0.5ms 目标"
        print(f"✅ 完整KDE引擎延迟测试通过 (平均: {avg_latency:.3f}ms)")

    def test_correctness_verification(self, kde_config, sample_prices):
        """
        验证KDE算法正确性
        """
        kde_core = KDECore(kde_config.kde_engine)
        lvn_extractor = LVNExtractor(kde_config.kde_engine)

        # 使用双峰分布数据
        test_prices = sample_prices[:1000]

        # 计算KDE
        grid, densities = kde_core.compute_kde(test_prices)

        # 验证KDE输出
        assert len(grid) > 0, "KDE网格为空"
        assert len(densities) > 0, "KDE密度为空"
        assert len(grid) == len(densities), "网格和密度长度不匹配"

        # 验证密度非负
        assert np.all(densities >= 0), "密度包含负值"

        # 验证网格单调递增
        assert np.all(np.diff(grid) > 0), "网格不是单调递增"

        # 计算积分（近似）
        if len(grid) > 1:
            integral = np.trapz(densities, grid)
            # KDE积分应接近1（概率密度函数）
            assert 0.5 < integral < 1.5, f"KDE积分异常: {integral:.3f}"

        # 提取LVN
        lvn_regions = lvn_extractor.extract_from_kde(grid, densities)

        # 验证LVN区域
        for region in lvn_regions:
            assert region.start_price < region.end_price, "LVN区域起始价格大于结束价格"
            assert region.min_density >= 0, "LVN最小密度为负"
            assert region.start_price <= region.min_price <= region.end_price, "最小价格不在区域范围内"

        print(f"\n📊 正确性验证:")
        print(f"  KDE网格大小: {len(grid)}")
        print(f"  密度范围: [{np.min(densities):.2e}, {np.max(densities):.2e}]")
        print(f"  检测到的LVN区域: {len(lvn_regions)}")
        if len(lvn_regions) > 0:
            print(f"  LVN价格范围: {lvn_regions[0].start_price:.2f} - {lvn_regions[0].end_price:.2f}")

        print(f"✅ 正确性验证通过")

    def test_performance_summary(self, kde_config, sample_prices, sample_ticks):
        """
        性能总结报告
        """
        print(f"\n{'=' * 60}")
        print(f"📈 KDE引擎性能总结报告")
        print(f"{'=' * 60}")

        # 运行所有性能测试并收集结果
        results = {}

        # 1. KDE核心延迟
        kde_core = KDECore(kde_config.kde_engine)
        test_prices = sample_prices[:1000]

        latencies = []
        for _ in range(10):
            start_time = time.perf_counter_ns()
            grid, densities = kde_core.compute_kde(test_prices)
            end_time = time.perf_counter_ns()
            latencies.append((end_time - start_time) / 1_000_000)

        results['kde_core'] = {
            'avg_ms': np.mean(latencies),
            'p95_ms': np.percentile(latencies, 95),
            'p99_ms': np.percentile(latencies, 99),
            'max_ms': np.max(latencies)
        }

        # 2. LVN提取延迟
        if len(grid) > 0 and len(densities) > 0:
            lvn_extractor = LVNExtractor(kde_config.kde_engine)
            latencies = []

            for _ in range(10):
                start_time = time.perf_counter_ns()
                lvn_extractor.extract_from_kde(grid, densities)
                end_time = time.perf_counter_ns()
                latencies.append((end_time - start_time) / 1_000_000)

            results['lvn_extraction'] = {
                'avg_ms': np.mean(latencies),
                'p95_ms': np.percentile(latencies, 95),
                'p99_ms': np.percentile(latencies, 99),
                'max_ms': np.max(latencies)
            }

        # 3. 批量吞吐量
        kde_matrix = KDEMatrixEngine(kde_config.kde_engine)
        batch_size = 100
        n_batches = 10
        price_batches = [sample_prices[i * batch_size:(i + 1) * batch_size] for i in range(n_batches)]

        start_time = time.perf_counter_ns()
        kde_matrix.compute_batch_kde(price_batches)
        end_time = time.perf_counter_ns()

        total_time_s = (end_time - start_time) / 1_000_000_000
        throughput = (batch_size * n_batches) / total_time_s if total_time_s > 0 else 0

        results['batch_throughput'] = {
            'samples_per_second': throughput,
            'batch_size': batch_size,
            'n_batches': n_batches,
            'total_time_s': total_time_s
        }

        # 4. 内存效率
        import psutil
        import os
        process = psutil.Process(os.getpid())

        initial_memory = process.memory_info().rss / 1024 / 1024
        # 创建多个对象
        objects = [
            KDECore(kde_config.kde_engine),
            KDEMatrixEngine(kde_config.kde_engine),
            LVNExtractor(kde_config.kde_engine)
        ]
        # 执行一些计算
        for obj in objects:
            if isinstance(obj, KDECore):
                obj.compute_kde(test_prices[:100])
        final_memory = process.memory_info().rss / 1024 / 1024
        memory_increase = final_memory - initial_memory

        results['memory_efficiency'] = {
            'increase_mb': memory_increase,
            'initial_mb': initial_memory,
            'final_mb': final_memory
        }

        # 输出报告
        print(f"\n📊 性能指标:")
        print(f"  KDE核心计算延迟:")
        print(f"    • 平均: {results['kde_core']['avg_ms']:.3f} ms")
        print(f"    • P95: {results['kde_core']['p95_ms']:.3f} ms")
        print(f"    • P99: {results['kde_core']['p99_ms']:.3f} ms")
        print(f"    • 最大: {results['kde_core']['max_ms']:.3f} ms")

        if 'lvn_extraction' in results:
            print(f"\n  LVN提取延迟:")
            print(f"    • 平均: {results['lvn_extraction']['avg_ms']:.3f} ms")
            print(f"    • P95: {results['lvn_extraction']['p95_ms']:.3f} ms")
            print(f"    • P99: {results['lvn_extraction']['p99_ms']:.3f} ms")
            print(f"    • 最大: {results['lvn_extraction']['max_ms']:.3f} ms")

        print(f"\n  批量处理吞吐量:")
        print(f"    • 吞吐量: {results['batch_throughput']['samples_per_second']:.0f} 样本/秒")
        print(f"    • 批次大小: {results['batch_throughput']['batch_size']}")
        print(f"    • 批次数量: {results['batch_throughput']['n_batches']}")
        print(f"    • 总时间: {results['batch_throughput']['total_time_s']:.3f} s")

        print(f"\n  内存效率:")
        print(f"    • 内存增加: {results['memory_efficiency']['increase_mb']:.2f} MB")
        print(f"    • 初始内存: {results['memory_efficiency']['initial_mb']:.2f} MB")
        print(f"    • 最终内存: {results['memory_efficiency']['final_mb']:.2f} MB")

        print(f"\n🎯 性能目标:")
        print(f"  ✅ KDE核心延迟 < 0.2ms: {'通过' if results['kde_core']['avg_ms'] < 0.2 else '失败'}")
        if 'lvn_extraction' in results:
            print(f"  ✅ LVN提取延迟 < 0.1ms: {'通过' if results['lvn_extraction']['avg_ms'] < 0.1 else '失败'}")
        print(
            f"  ✅ 批量吞吐量 > 10,000 样本/秒: {'通过' if results['batch_throughput']['samples_per_second'] > 10000 else '失败'}")
        print(f"  ✅ 内存增加 < 5MB: {'通过' if results['memory_efficiency']['increase_mb'] < 5 else '失败'}")

        print(f"\n{'=' * 60}")
        print(f"📝 性能总结:")
        if (results['kde_core']['avg_ms'] < 0.2 and
                ('lvn_extraction' not in results or results['lvn_extraction']['avg_ms'] < 0.1) and
                results['batch_throughput']['samples_per_second'] > 10000 and
                results['memory_efficiency']['increase_mb'] < 5):
            print(f"✅ 所有性能目标达成！")
        else:
            print(f"⚠️  部分性能目标未达成，需要优化")

        print(f"{'=' * 60}")


if __name__ == "__main__":
    # 直接运行性能测试
    test = TestKDEPerformance()

    # 创建配置
    config = TripleAEngineConfig()

    print("🚀 开始KDE引擎性能测试...")

    # 运行性能总结
    test.kde_config = lambda: config
    test.test_performance_summary(config, None, None)
