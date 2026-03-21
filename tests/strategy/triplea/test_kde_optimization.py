#!/usr/bin/env python3
"""
KDE优化测试
分析Numba JIT编译后的KDE计算性能
"""

import os
import sys
import time

import numpy as np

# 获取项目根目录并添加到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

from src.strategy.triplea.data_structures import TripleAEngineConfig
from src.strategy.triplea.kde_core import KDECore, kde_density_1d


def test_kde_performance():
    """测试KDE计算性能"""
    config = TripleAEngineConfig()
    kde_core = KDECore(config.kde_engine)

    # 创建测试数据
    np.random.seed(42)
    test_sizes = [100, 500, 1000, 2000, 5000]

    print("📊 KDE计算性能分析")
    print("=" * 60)

    for size in test_sizes:
        prices = np.random.randn(size) * 50 + 3000

        print(f"\n数据大小: {size} 个样本")

        # 首次运行（包含编译开销）
        start_time = time.perf_counter_ns()
        grid, densities = kde_core.compute_kde(prices)
        first_run_time = (time.perf_counter_ns() - start_time) / 1_000_000
        print(f"  首次运行: {first_run_time:.3f}ms (包含编译)")

        # 后续运行（预热后）
        times = []
        for i in range(10):
            start_time = time.perf_counter_ns()
            kde_core.compute_kde(prices)
            end_time = time.perf_counter_ns()
            times.append((end_time - start_time) / 1_000_000)

        avg_time = np.mean(times)
        min_time = np.min(times)
        max_time = np.max(times)
        p95_time = np.percentile(times, 95)

        print(f"  平均延迟: {avg_time:.3f}ms")
        print(f"  最小时延: {min_time:.3f}ms")
        print(f"  最大延迟: {max_time:.3f}ms")
        print(f"  P95延迟: {p95_time:.3f}ms")

        if avg_time > 0.2:
            print(f"  ⚠️  未达到0.2ms性能目标")


def test_numba_warmup():
    """测试Numba预热效果"""
    print("\n" + "=" * 60)
    print("🔥 Numba JIT预热效果测试")
    print("=" * 60)

    # 创建测试数据
    np.random.seed(42)
    prices = np.random.randn(1000) * 50 + 3000
    grid = np.linspace(2900, 3100, 200)
    bandwidth = 5.0

    print(f"\n样本数量: 1000, 网格大小: 200")

    # 首次运行（编译）
    start_time = time.perf_counter_ns()
    densities1 = kde_density_1d(prices, grid, bandwidth)
    first_run_time = (time.perf_counter_ns() - start_time) / 1_000_000
    print(f"  首次编译运行: {first_run_time:.3f}ms")

    # 第二次运行
    start_time = time.perf_counter_ns()
    densities2 = kde_density_1d(prices, grid, bandwidth)
    second_run_time = (time.perf_counter_ns() - start_time) / 1_000_000
    print(f"  第二次运行: {second_run_time:.3f}ms")

    # 连续运行10次
    times = []
    for i in range(10):
        start_time = time.perf_counter_ns()
        kde_density_1d(prices, grid, bandwidth)
        end_time = time.perf_counter_ns()
        times.append((end_time - start_time) / 1_000_000)

    avg_time = np.mean(times)
    min_time = np.min(times)

    print(f"  10次平均延迟: {avg_time:.3f}ms")
    print(f"  最佳单次延迟: {min_time:.3f}ms")

    if min_time > 0.2:
        print(f"  ⚠️  KDE核心计算延迟过高")


def analyze_bottlenecks():
    """分析性能瓶颈"""
    print("\n" + "=" * 60)
    print("🔍 性能瓶颈分析")
    print("=" * 60)

    # 导入所有相关函数
    from src.strategy.triplea.kde_core import silverman_bandwidth, compute_density_percentiles

    config = TripleAEngineConfig()
    kde_core = KDECore(config.kde_engine)

    # 创建测试数据
    np.random.seed(42)
    prices = np.random.randn(1000) * 50 + 3000

    print("\n1. 带宽计算性能")
    start_time = time.perf_counter_ns()
    bandwidth = silverman_bandwidth(prices)
    bw_time = (time.perf_counter_ns() - start_time) / 1_000_000
    print(f"  带宽计算时间: {bw_time:.3f}ms")
    print(f"  带宽值: {bandwidth:.3f}")

    print("\n2. 网格创建性能")
    start_time = time.perf_counter_ns()
    grid = kde_core._create_grid(prices)
    grid_time = (time.perf_counter_ns() - start_time) / 1_000_000
    print(f"  网格创建时间: {grid_time:.3f}ms")
    print(f"  网格大小: {len(grid)}")

    print("\n3. KDE密度计算性能")
    # 预热
    kde_density_1d(prices[:100], grid[:50], bandwidth)

    start_time = time.perf_counter_ns()
    densities = kde_density_1d(prices, grid, bandwidth)
    kde_time = (time.perf_counter_ns() - start_time) / 1_000_000
    print(f"  KDE计算时间: {kde_time:.3f}ms")
    print(f"  密度大小: {len(densities)}")

    print("\n4. 百分位数计算性能")
    percentiles = np.array([30.0])
    # 预热compute_density_percentiles函数
    compute_density_percentiles(densities[:10], percentiles)
    start_time = time.perf_counter_ns()
    threshold = compute_density_percentiles(densities, percentiles)
    perc_time = (time.perf_counter_ns() - start_time) / 1_000_000
    print(f"  百分位数计算时间: {perc_time:.3f}ms")
    print(f"  阈值: {threshold[0]:.6f}")

    total_time = bw_time + grid_time + kde_time + perc_time
    print(f"\n💡 总计算时间: {total_time:.3f}ms")
    print(
        f"💡 主要瓶颈: {'带宽计算' if bw_time / total_time > 0.5 else 'KDE计算' if kde_time / total_time > 0.5 else '其他'}")


if __name__ == "__main__":
    print("🚀 开始KDE性能优化分析")

    test_kde_performance()
    test_numba_warmup()
    analyze_bottlenecks()

    print("\n" + "=" * 60)
    print("📝 分析总结:")
    print("  1. Numba首次编译开销显著")
    print("  2. 预热后性能可能达到目标")
    print("  3. 需要实现Numba预热策略")
    print("  4. 考虑缓存计算带宽")
    print("=" * 60)
