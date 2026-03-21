#!/usr/bin/env python3
"""
验证KDE算法修改的数值等价性测试
比较原始循环算法和Numpy广播算法的结果差异
"""

import sys

import numpy as np


def kde_original(prices, bandwidth=0.5, grid_size=100):
    """原始循环算法"""
    if len(prices) == 0:
        return np.array([]), np.array([])

    # 创建评估网格
    grid_points = np.linspace(
        prices.min(), prices.max(),
        min(grid_size, len(prices))
    )

    n = len(prices)
    kde_values = np.zeros_like(grid_points)

    # 原始Python循环版本
    for i, x in enumerate(grid_points):
        kernel_sum = np.sum(np.exp(-0.5 * ((prices - x) / bandwidth) ** 2))
        kde_values[i] = kernel_sum / (n * bandwidth * np.sqrt(2 * np.pi))

    return grid_points, kde_values


def kde_vectorized(prices, bandwidth=0.5, grid_size=100):
    """向量化Numpy广播算法"""
    if len(prices) == 0:
        return np.array([]), np.array([])

    # 创建评估网格
    grid_points = np.linspace(
        prices.min(), prices.max(),
        min(grid_size, len(prices))
    )

    n = len(prices)

    # 向量化计算
    diff = prices[:, np.newaxis] - grid_points  # 形状 (n, m)
    kernel = np.exp(-0.5 * (diff / bandwidth) ** 2)  # 形状 (n, m)
    kde_values = np.sum(kernel, axis=0) / (n * bandwidth * np.sqrt(2 * np.pi))  # 形状 (m,)

    return grid_points, kde_values


def run_equivalence_test():
    """运行等价性测试并返回结果（用于脚本模式）"""
    print("🔬 KDE算法等价性测试")
    print("=" * 50)

    # 测试不同规模的数据
    test_cases = [
        ("小型数据集", np.random.randn(10)),
        ("中型数据集", np.random.randn(100)),
        ("大型数据集", np.random.randn(1000)),
        ("极端值测试", np.array([1e6, -1e6, 0, 1e-6, -1e-6])),
        ("重复值测试", np.array([1.0, 1.0, 2.0, 2.0, 2.0])),
    ]

    all_pass = True
    bandwidth = 0.5

    for name, prices in test_cases:
        print(f"\n📊 测试: {name} (n={len(prices)})")

        # 计算两个版本的结果
        grid1, kde1 = kde_original(prices, bandwidth)
        grid2, kde2 = kde_vectorized(prices, bandwidth)

        # 检查网格点是否相同
        grid_diff = np.max(np.abs(grid1 - grid2))
        print(f"  网格点差异: {grid_diff:.2e}")

        # 检查KDE值差异
        if len(kde1) > 0 and len(kde2) > 0:
            abs_diff = np.max(np.abs(kde1 - kde2))
            rel_diff = np.max(np.abs((kde1 - kde2) / (kde1 + 1e-15)))  # 避免除零

            print(f"  绝对差异 (max): {abs_diff:.2e}")
            print(f"  相对差异 (max): {rel_diff:.2e}")

            # 检查是否在数值误差范围内
            tolerance = 1e-10  # 双精度浮点数的典型误差范围
            if abs_diff < tolerance and rel_diff < tolerance:
                print(f"  ✅ 通过: 差异在容差范围内 ({tolerance})")
            else:
                print(f"  ⚠️ 警告: 差异超过容差范围")
                all_pass = False
        else:
            print(f"  ✅ 通过: 空结果一致")

    # 测试性能对比
    print("\n⚡ 性能对比测试")
    print("-" * 30)

    large_prices = np.random.randn(5000)
    import time

    # 原始算法性能
    start = time.perf_counter()
    for _ in range(10):
        kde_original(large_prices, bandwidth)
    orig_time = time.perf_counter() - start

    # 向量化算法性能
    start = time.perf_counter()
    for _ in range(10):
        kde_vectorized(large_prices, bandwidth)
    vec_time = time.perf_counter() - start

    print(f"  原始算法时间: {orig_time:.3f}s")
    print(f"  向量化算法时间: {vec_time:.3f}s")
    print(f"  性能提升: {orig_time / vec_time:.1f}倍")

    # 总结
    print("\n" + "=" * 50)
    if all_pass:
        print("✅ 所有测试通过：算法在数值上等价")
    else:
        print("⚠️ 部分测试未通过：算法存在显著差异")
        print("   建议检查数值稳定性")

    return all_pass


def test_equivalence():
    """pytest兼容的等价性测试（不返回任何值，使用assert）"""
    # 运行测试并获取结果
    all_pass = run_equivalence_test()

    # 使用assert验证测试结果
    assert all_pass, "KDE算法等价性测试失败：原始算法和向量化算法结果存在显著差异"


def run_realistic_scenarios_test():
    """运行实盘场景测试（用于脚本模式）"""
    print("\n🎯 实盘场景测试")
    print("=" * 50)

    # 模拟实盘价格数据（ETH-USDT典型价格）
    np.random.seed(42)  # 可重复测试

    # 场景1：正常市场波动
    base_price = 3000.0
    volatility = 50.0  # 50美元波动
    normal_prices = base_price + np.random.randn(1000) * volatility

    # 场景2：窄幅震荡（积累阶段）
    narrow_prices = base_price + np.random.randn(500) * 5.0  # 5美元波动

    # 场景3：大幅波动（突破阶段）
    volatile_prices = base_price + np.random.randn(200) * 200.0  # 200美元波动

    scenarios = [
        ("正常市场", normal_prices),
        ("窄幅震荡", narrow_prices),
        ("大幅波动", volatile_prices),
    ]

    bandwidth = 0.5

    all_pass = True

    for name, prices in scenarios:
        print(f"\n📈 场景: {name} (价格范围: {prices.min():.1f} - {prices.max():.1f})")

        grid1, kde1 = kde_original(prices, bandwidth)
        grid2, kde2 = kde_vectorized(prices, bandwidth)

        # 检查关键特征
        if len(kde1) > 0:
            # 1. 峰值位置（LVN检测的关键）
            peak_idx1 = np.argmax(kde1)
            peak_idx2 = np.argmax(kde2)
            peak_pos1 = grid1[peak_idx1]
            peak_pos2 = grid2[peak_idx2]

            # 2. 低点位置（可能对应支撑/阻力）
            # 寻找局部最小值（简化：寻找密度最低的点）
            min_idx1 = np.argmin(kde1)
            min_idx2 = np.argmin(kde2)
            min_pos1 = grid1[min_idx1]
            min_pos2 = grid2[min_idx2]

            print(f"  峰值位置: 原始={peak_pos1:.2f}, 向量化={peak_pos2:.2f}, 差异={abs(peak_pos1 - peak_pos2):.2f}")
            print(f"  低点位置: 原始={min_pos1:.2f}, 向量化={min_pos2:.2f}, 差异={abs(min_pos1 - min_pos2):.2f}")

            # 检查是否影响交易决策
            price_tick = 0.1  # 假设最小价格变动单位
            if abs(peak_pos1 - peak_pos2) < price_tick and abs(min_pos1 - min_pos2) < price_tick:
                print(f"  ✅ 对交易决策无影响（差异 < {price_tick} tick）")
            else:
                print(f"  ⚠️ 可能影响交易决策（差异 ≥ {price_tick} tick）")
                all_pass = False

    print("\n" + "=" * 50)
    print("💡 实盘影响分析：")
    print("1. 如果峰值/低点位置差异小于最小价格变动单位，不影响交易")
    print("2. KDE主要用于检测LVN区域，微小差异通常可接受")
    print("3. 建议在实盘前进行回测验证")

    return all_pass


def test_realistic_scenarios():
    """pytest兼容的实盘场景测试"""
    # 运行测试并获取结果
    all_pass = run_realistic_scenarios_test()

    # 使用assert验证测试结果
    assert all_pass, "实盘场景测试失败：算法差异可能影响交易决策"


if __name__ == "__main__":
    print("🚀 KDE算法修改验证工具")
    print("=" * 50)

    # 运行等价性测试
    if run_equivalence_test():
        # 如果基本等价，运行实盘场景测试
        run_realistic_scenarios_test()
        sys.exit(0)
    else:
        print("\n❌ 算法不等价，需要进一步检查")
        sys.exit(1)
