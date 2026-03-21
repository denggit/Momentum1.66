#!/usr/bin/env python3
"""
测试百分位数计算性能
"""

import sys
import time

import numpy as np

sys.path.insert(0, '../../..')

from src.strategy.triplea.kde_core import compute_density_percentiles


def _test_percentile_performance():
    """测试百分位数计算性能（内部函数，不是pytest测试）"""
    np.random.seed(42)

    # 创建测试数据（200个点，与KDE网格大小相同）
    n_tests = 1000
    densities = np.random.random(200) * 100

    print(f"📊 百分位数计算性能测试")
    print(f"数据大小: {len(densities)}")
    print(f"测试次数: {n_tests}")

    # 预热
    for _ in range(10):
        compute_density_percentiles(densities, np.array([30.0]))

    # 性能测试
    times = []
    for i in range(n_tests):
        start_time = time.perf_counter_ns()
        result = compute_density_percentiles(densities, np.array([30.0]))
        end_time = time.perf_counter_ns()
        times.append((end_time - start_time) / 1_000_000)  # ms

    avg_time = np.mean(times)
    min_time = np.min(times)
    max_time = np.max(times)
    p95_time = np.percentile(times, 95)

    print(f"\n结果:")
    print(f"  平均延迟: {avg_time:.3f}ms")
    print(f"  最小时延: {min_time:.3f}ms")
    print(f"  最大延迟: {max_time:.3f}ms")
    print(f"  P95延迟: {p95_time:.3f}ms")

    # 与np.percentile比较
    print(f"\n对比np.percentile:")
    times_np = []
    for i in range(n_tests):
        start_time = time.perf_counter_ns()
        result = np.percentile(densities, 30)
        end_time = time.perf_counter_ns()
        times_np.append((end_time - start_time) / 1_000_000)

    avg_time_np = np.mean(times_np)
    print(f"  np.percentile平均延迟: {avg_time_np:.3f}ms")

    return avg_time


if __name__ == "__main__":
    _test_percentile_performance()
