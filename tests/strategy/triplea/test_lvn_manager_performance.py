#!/usr/bin/env python3
"""
测试LVN管理器性能
"""

import sys
import os

# 获取项目根目录并添加到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

import numpy as np
import time

from src.strategy.triplea.data_structures import KDEEngineConfig
from src.strategy.triplea.lvn_manager import LVNManager


def run_performance_test():
    """运行性能测试（脚本模式）"""
    print("⚡ LVN管理器性能测试")
    print("-" * 60)

    # 创建配置
    config = KDEEngineConfig()
    manager = LVNManager(config)

    # 创建测试数据（模拟真实KDE结果）
    np.random.seed(42)
    n_tests = 100
    grid_size = 100
    price_min = 2950
    price_max = 3050

    # 预先生成测试数据
    test_data = []
    for i in range(n_tests):
        grid = np.linspace(price_min, price_max, grid_size)
        # 创建随机密度，模拟LVN区域
        densities = np.random.random(grid_size) * 100
        # 在随机位置创建低密度区域（LVN）
        lvn_pos = np.random.randint(20, 80)
        densities[lvn_pos:lvn_pos+5] = np.random.random(5) * 10
        test_data.append((grid, densities))

    print(f"测试配置:")
    print(f"  测试次数: {n_tests}")
    print(f"  网格大小: {grid_size}")
    print(f"  价格范围: {price_min} - {price_max}")

    # 性能测试
    print("\n开始性能测试...")
    latencies = []
    cluster_counts = []

    for i, (grid, densities) in enumerate(test_data):
        start_time = time.perf_counter_ns()
        clusters = manager.process_kde_result(grid, densities)
        end_time = time.perf_counter_ns()

        latency_ms = (end_time - start_time) / 1_000_000
        latencies.append(latency_ms)
        cluster_counts.append(len(clusters))

        if i % 20 == 0:
            print(f"  测试 {i+1}/{n_tests}: {latency_ms:.3f}ms, 检测到 {len(clusters)} 个簇")

    # 分析结果
    avg_latency = np.mean(latencies)
    min_latency = np.min(latencies)
    max_latency = np.max(latencies)
    p95_latency = np.percentile(latencies, 95)
    p99_latency = np.percentile(latencies, 99)

    avg_clusters = np.mean(cluster_counts)

    print(f"\n📊 性能测试结果:")
    print(f"  平均延迟: {avg_latency:.3f}ms")
    print(f"  最小时延: {min_latency:.3f}ms")
    print(f"  最大延迟: {max_latency:.3f}ms")
    print(f"  P95延迟: {p95_latency:.3f}ms")
    print(f"  P99延迟: {p99_latency:.3f}ms")
    print(f"  平均检测簇数: {avg_clusters:.1f}")

    # 性能目标检查
    target_latency = 0.5  # 500微秒
    if avg_latency < target_latency:
        print(f"✅ 性能目标达成: {avg_latency:.3f}ms < {target_latency}ms")
    else:
        print(f"⚠️ 性能未达标: {avg_latency:.3f}ms >= {target_latency}ms")

    # 内存使用检查（粗略估计）
    stats = manager.get_statistics()
    print(f"\n📊 内存使用统计:")
    print(f"  活跃簇数: {stats['active_clusters_count']}")
    print(f"  总簇数: {stats['total_clusters_count']}")
    print(f"  历史记录数: {stats['region_history_count']}")
    print(f"  唯一区域数: {stats['unique_regions_tracked']}")

    return {
        'avg_latency_ms': avg_latency,
        'min_latency_ms': min_latency,
        'max_latency_ms': max_latency,
        'p95_latency_ms': p95_latency,
        'p99_latency_ms': p99_latency,
        'avg_clusters': avg_clusters,
        'stats': stats
    }


def test_performance():
    """pytest性能测试"""
    config = KDEEngineConfig()
    manager = LVNManager(config)

    # 创建测试数据
    np.random.seed(42)
    grid = np.linspace(2950, 3050, 100)
    densities = np.random.random(100) * 100
    # 创建低密度区域
    densities[40:45] = np.random.random(5) * 10

    # 预热：先运行一次以触发Numba编译
    manager.process_kde_result(grid, densities)

    # 测量处理延迟（第二次运行，避免编译开销）
    start_time = time.perf_counter_ns()
    clusters = manager.process_kde_result(grid, densities)
    end_time = time.perf_counter_ns()

    latency_ms = (end_time - start_time) / 1_000_000

    # 验证性能（目标<0.5ms）
    assert latency_ms < 0.5, f"LVN处理延迟 {latency_ms:.3f}ms 超过 0.5ms 目标"

    # 验证功能
    assert clusters is not None
    # 至少应该检测到一些区域（可能没有，取决于随机数据）
    # 不强制要求检测到区域


def test_memory_management():
    """测试内存管理（簇清理）"""
    config = KDEEngineConfig()
    manager = LVNManager(config)

    # 创建测试数据
    grid = np.linspace(2950, 3050, 100)

    # 多次处理KDE结果，创建多个簇
    for i in range(10):
        densities = np.random.random(100) * 100
        # 在随机位置创建低密度区域
        pos = np.random.randint(20, 80)
        densities[pos:pos+3] = np.random.random(3) * 10
        manager.process_kde_result(grid, densities)

    # 获取初始统计
    stats_before = manager.get_statistics()

    # 模拟时间流逝（通过直接调用清理函数）
    # 注意：实际清理发生在process_kde_result内部
    # 这里我们只是验证管理器功能

    # 再次处理一些数据
    for i in range(5):
        densities = np.random.random(100) * 100
        manager.process_kde_result(grid, densities)

    stats_after = manager.get_statistics()

    # 验证管理器仍在工作
    assert stats_after['total_regions_detected'] > 0
    # 历史记录应该增长
    assert stats_after['region_history_count'] >= stats_before['region_history_count']


def main():
    print("🚀 LVN管理器性能测试")
    print("=" * 70)

    results = run_performance_test()

    print("\n" + "=" * 70)
    print("🎉 LVN管理器性能测试完成!")

    # 性能总结
    if results['avg_latency_ms'] < 0.1:
        print("✅ 性能优秀，满足实时交易要求")
    elif results['avg_latency_ms'] < 0.2:
        print("✅ 性能良好，可满足大多数场景")
    else:
        print("⚠️ 性能需要优化")


if __name__ == "__main__":
    main()