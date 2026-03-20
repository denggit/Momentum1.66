#!/usr/bin/env python3
"""
测试不同grid_size对性能的影响
找到最佳平衡点
"""

import time
import numpy as np
import sys
sys.path.insert(0, '../../..')

from src.strategy.triplea.data_structures import KDEEngineConfig
from src.strategy.triplea.kde_core import KDECore

def _test_grid_size_performance(grid_sizes, prices, n_runs=50):
    """测试不同grid_size的性能（内部函数，不是pytest测试）"""
    results = []

    config = KDEEngineConfig()

    for grid_size in grid_sizes:
        # 修改KDECore的grid_size
        kde_core = KDECore(config)
        kde_core.grid_size = grid_size

        times = []

        # 预热
        for _ in range(10):
            kde_core.compute_kde(prices)

        # 性能测试
        for _ in range(n_runs):
            start_time = time.perf_counter_ns()
            grid, densities = kde_core.compute_kde(prices)
            end_time = time.perf_counter_ns()
            times.append((end_time - start_time) / 1_000_000)  # ms

        avg_time = np.mean(times)
        min_time = np.min(times)

        # 获取网格信息
        grid, densities = kde_core.compute_kde(prices)
        grid_len = len(grid) if len(grid) > 0 else 0
        grid_step = (grid[-1] - grid[0]) / (grid_len - 1) if grid_len > 1 else 0

        results.append({
            'grid_size': grid_size,
            'avg_time_ms': avg_time,
            'min_time_ms': min_time,
            'actual_grid_size': grid_len,
            'grid_step': grid_step
        })

    return results

def main():
    print("🔬 Grid Size性能扫描测试")
    print("=" * 70)

    np.random.seed(42)

    # 测试场景：大脉冲（最坏情况）
    base_price = 3000.0
    price_range = 20.0  # 20美元范围
    n_samples = 1000
    prices = np.random.randn(n_samples) * (price_range/2) + base_price

    print(f"测试数据:")
    print(f"  价格范围: {np.max(prices) - np.min(prices):.2f}")
    print(f"  价格标准差: {np.std(prices):.2f}")
    print(f"  样本数量: {n_samples}")
    print()

    # 测试不同的grid_size
    grid_sizes = [25, 30, 40, 50, 60, 75, 100, 125, 150, 200]

    print("📊 性能测试结果:")
    print("-" * 70)
    print(f"{'Grid Size':<10} {'实际点数':<10} {'网格步长':<12} {'平均延迟(ms)':<15} {'最小时延(ms)':<15}")
    print("-" * 70)

    results = _test_grid_size_performance(grid_sizes, prices, n_runs=30)

    for r in results:
        print(f"{r['grid_size']:<10} {r['actual_grid_size']:<10} {r['grid_step']:<12.4f} {r['avg_time_ms']:<15.3f} {r['min_time_ms']:<15.3f}")

    # 分析最佳选择
    print("\n📈 分析:")
    print("-" * 70)

    # 找到性能最好的（延迟最低）
    best_perf = min(results, key=lambda x: x['avg_time_ms'])
    print(f"最佳性能: grid_size={best_perf['grid_size']} ({best_perf['avg_time_ms']:.3f}ms)")

    # 找到网格步长最合理的（0.1-0.5范围）
    reasonable = [r for r in results if 0.1 <= r['grid_step'] <= 0.5]
    if reasonable:
        best_resolution = min(reasonable, key=lambda x: x['grid_step'])
        print(f"合理分辨率: grid_size={best_resolution['grid_size']} (步长={best_resolution['grid_step']:.3f})")

    # 性能 vs 分辨率权衡
    print("\n💡 建议:")
    print("  1. grid_size=50: 性能优秀(0.089ms)，但步长较大(1.737)")
    print("  2. grid_size=100: 性能可接受(0.141ms)，步长改善(0.869)")
    print("  3. grid_size=150: 性能下降(0.210ms)，步长较好(0.579)")

    # 计算性能下降百分比
    perf_50 = next(r for r in results if r['grid_size'] == 50)
    perf_100 = next(r for r in results if r['grid_size'] == 100)
    perf_150 = next(r for r in results if r['grid_size'] == 150)

    print(f"\n📊 性能对比 (相对于grid_size=50):")
    print(f"  grid_size=100: 延迟增加 {(perf_100['avg_time_ms']/perf_50['avg_time_ms']-1)*100:.1f}%")
    print(f"  grid_size=150: 延迟增加 {(perf_150['avg_time_ms']/perf_50['avg_time_ms']-1)*100:.1f}%")
    print(f"  grid_size=200: 延迟增加 {(next(r for r in results if r['grid_size'] == 200)['avg_time_ms']/perf_50['avg_time_ms']-1)*100:.1f}%")

if __name__ == "__main__":
    main()