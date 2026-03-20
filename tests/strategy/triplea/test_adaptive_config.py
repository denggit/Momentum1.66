#!/usr/bin/env python3
"""
测试自适应网格配置功能
验证KDEEngineConfig新增参数和KDECore自适应逻辑
"""

import numpy as np
import sys
import os

# 获取项目根目录并添加到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

from src.strategy.triplea.data_structures import KDEEngineConfig, TripleAEngineConfig
from src.strategy.triplea.kde_core import KDECore

def test_config_defaults():
    """测试配置默认值"""
    print("🔧 测试配置默认值")
    print("-" * 60)

    # 测试KDEEngineConfig默认值
    kde_config = KDEEngineConfig()
    print("KDEEngineConfig 默认值:")
    print(f"  adaptive_grid: {kde_config.adaptive_grid} (应为: True)")
    print(f"  target_grid_step: {kde_config.target_grid_step} (应为: 0.2)")
    print(f"  min_grid_size: {kde_config.min_grid_size} (应为: 30)")
    print(f"  max_grid_size: {kde_config.max_grid_size} (应为: 80)")

    assert kde_config.adaptive_grid == True, "adaptive_grid 默认值应为 True"
    assert kde_config.target_grid_step == 0.2, "target_grid_step 默认值应为 0.2"
    assert kde_config.min_grid_size == 30, "min_grid_size 默认值应为 30"
    assert kde_config.max_grid_size == 80, "max_grid_size 默认值应为 80"

    # 测试TripleAEngineConfig包含新配置
    engine_config = TripleAEngineConfig()
    print(f"\nTripleAEngineConfig.kde_engine.adaptive_grid: {engine_config.kde_engine.adaptive_grid}")
    assert engine_config.kde_engine.adaptive_grid == True

    print("✅ 配置默认值测试通过")

def test_adaptive_vs_fixed():
    """测试自适应 vs 固定网格策略"""
    print("\n🔬 测试自适应 vs 固定网格策略")
    print("-" * 60)

    np.random.seed(42)

    # 场景1：小脉冲
    small_pulse = np.random.randn(1000) * 0.25 + 3000
    price_range_small = np.max(small_pulse) - np.min(small_pulse)

    # 场景2：大脉冲
    large_pulse = np.random.randn(1000) * 25 + 3000
    price_range_large = np.max(large_pulse) - np.min(large_pulse)

    print(f"小脉冲场景: 价格范围={price_range_small:.2f}")
    print(f"大脉冲场景: 价格范围={price_range_large:.2f}")

    # 创建配置
    config_adaptive = KDEEngineConfig(adaptive_grid=True)
    config_fixed = KDEEngineConfig(adaptive_grid=False)

    # 创建KDE核心
    kde_adaptive = KDECore(config_adaptive)
    kde_fixed = KDECore(config_fixed)

    # 测试小脉冲
    print("\n📊 小脉冲场景测试:")
    grid_adaptive_small, _ = kde_adaptive.compute_kde(small_pulse)
    grid_fixed_small, _ = kde_fixed.compute_kde(small_pulse)

    step_adaptive_small = (grid_adaptive_small[-1] - grid_adaptive_small[0]) / (len(grid_adaptive_small) - 1)
    step_fixed_small = (grid_fixed_small[-1] - grid_fixed_small[0]) / (len(grid_fixed_small) - 1)

    print(f"  自适应: {len(grid_adaptive_small)}点, 步长={step_adaptive_small:.4f}")
    print(f"  固定: {len(grid_fixed_small)}点, 步长={step_fixed_small:.4f}")

    # 验证自适应策略是否限制了网格大小
    assert len(grid_adaptive_small) <= config_adaptive.max_grid_size
    assert len(grid_adaptive_small) >= config_adaptive.min_grid_size
    print(f"  自适应网格范围检查通过: [{len(grid_adaptive_small)}] ∈ [{config_adaptive.min_grid_size}, {config_adaptive.max_grid_size}]")

    # 测试大脉冲
    print("\n📊 大脉冲场景测试:")
    grid_adaptive_large, _ = kde_adaptive.compute_kde(large_pulse)
    grid_fixed_large, _ = kde_fixed.compute_kde(large_pulse)

    step_adaptive_large = (grid_adaptive_large[-1] - grid_adaptive_large[0]) / (len(grid_adaptive_large) - 1)
    step_fixed_large = (grid_fixed_large[-1] - grid_fixed_large[0]) / (len(grid_fixed_large) - 1)

    print(f"  自适应: {len(grid_adaptive_large)}点, 步长={step_adaptive_large:.4f}")
    print(f"  固定: {len(grid_fixed_large)}点, 步长={step_fixed_large:.4f}")

    # 验证自适应策略是否限制了网格大小
    assert len(grid_adaptive_large) <= config_adaptive.max_grid_size
    assert len(grid_adaptive_large) >= config_adaptive.min_grid_size
    print(f"  自适应网格范围检查通过: [{len(grid_adaptive_large)}] ∈ [{config_adaptive.min_grid_size}, {config_adaptive.max_grid_size}]")

    # 验证步长差异
    print(f"\n📈 步长对比:")
    print(f"  小脉冲: 自适应({step_adaptive_small:.4f}) vs 固定({step_fixed_small:.4f})")
    print(f"  大脉冲: 自适应({step_adaptive_large:.4f}) vs 固定({step_fixed_large:.4f})")

    # 自适应步长应该更接近目标步长
    deviation_small = abs(step_adaptive_small - config_adaptive.target_grid_step)
    deviation_large = abs(step_adaptive_large - config_adaptive.target_grid_step)

    print(f"  自适应步长偏差: 小脉冲={deviation_small:.4f}, 大脉冲={deviation_large:.4f}")
    print("✅ 自适应 vs 固定网格策略测试通过")

def test_config_serialization():
    """测试配置序列化"""
    print("\n🔄 测试配置序列化")
    print("-" * 60)

    # 创建完整配置
    engine_config = TripleAEngineConfig()

    # 转换为字典
    config_dict = engine_config.to_dict()
    print("序列化字典中的KDE配置:")
    print(f"  adaptive_grid: {config_dict['kde_engine']['adaptive_grid']}")
    print(f"  target_grid_step: {config_dict['kde_engine']['target_grid_step']}")

    # 验证所有新字段都存在
    assert 'adaptive_grid' in config_dict['kde_engine']
    assert 'target_grid_step' in config_dict['kde_engine']
    assert 'min_grid_size' in config_dict['kde_engine']
    assert 'max_grid_size' in config_dict['kde_engine']

    print("✅ 配置序列化测试通过")

def test_performance_with_adaptive():
    """测试自适应网格的性能"""
    print("\n⚡ 测试自适应网格性能")
    print("-" * 60)

    import time

    np.random.seed(42)

    # 创建测试数据（典型脉冲范围）
    pulse_range = 15.0  # 15美元范围
    prices = np.random.randn(1000) * (pulse_range/2) + 3000

    # 测试自适应配置
    config_adaptive = KDEEngineConfig(adaptive_grid=True)
    kde_adaptive = KDECore(config_adaptive)

    # 预热
    for _ in range(10):
        kde_adaptive.compute_kde(prices)

    # 性能测试
    times = []
    for _ in range(30):
        start_time = time.perf_counter_ns()
        grid, densities = kde_adaptive.compute_kde(prices)
        end_time = time.perf_counter_ns()
        times.append((end_time - start_time) / 1_000_000)

    avg_time = np.mean(times)
    min_time = np.min(times)
    grid_size = len(grid)

    print(f"自适应网格性能:")
    print(f"  平均延迟: {avg_time:.3f}ms")
    print(f"  最小时延: {min_time:.3f}ms")
    print(f"  网格大小: {grid_size}点")
    print(f"  网格步长: {(grid[-1] - grid[0])/(grid_size-1):.4f}")

    # 验证性能目标（<0.2ms）
    assert avg_time < 0.2, f"平均延迟 {avg_time:.3f}ms 超过 0.2ms 目标"
    print(f"✅ 性能目标达成: {avg_time:.3f}ms < 0.2ms")

    # 验证网格步长合理性
    step = (grid[-1] - grid[0]) / (grid_size - 1)
    assert step >= 0.1, f"网格步长 {step:.4f} 可能过小，噪点风险"
    assert step <= 1.0, f"网格步长 {step:.4f} 可能过大，模糊风险"
    print(f"✅ 网格步长合理: {step:.4f} ∈ [0.1, 1.0]")

def main():
    print("🚀 自适应网格配置功能测试")
    print("=" * 70)

    test_config_defaults()
    test_adaptive_vs_fixed()
    test_config_serialization()
    test_performance_with_adaptive()

    print("\n" + "=" * 70)
    print("🎉 所有测试通过！自适应网格配置功能验证完成")
    print("\n💡 总结:")
    print("  1. 默认配置 adaptive_grid=True，目标步长=0.2")
    print("  2. 自适应策略能根据价格范围动态调整网格点数")
    print("  3. 网格点数限制在 [30, 80] 范围内")
    print("  4. 性能目标达成 (<0.2ms)")
    print("  5. 实盘保命配置已就绪！")

if __name__ == "__main__":
    main()