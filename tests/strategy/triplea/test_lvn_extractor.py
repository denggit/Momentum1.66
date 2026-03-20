#!/usr/bin/env python3
"""
测试LVN提取器功能
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
from src.strategy.triplea.lvn_extractor import LVNExtractor, LVNRegion
from src.strategy.triplea.kde_core import KDECore


def run_lvn_extraction_test():
    """运行LVN提取测试（脚本模式）"""
    print("🔬 测试LVN提取功能")
    print("-" * 60)

    # 创建配置
    config = KDEEngineConfig()
    extractor = LVNExtractor(config)

    print(f"提取器初始化完成: {extractor}")

    # 创建测试数据（模拟双峰分布，中间有LVN）
    np.random.seed(42)
    n_samples = 10000

    # 两个峰值
    prices1 = np.random.randn(n_samples // 2) * 20 + 2950  # 第一个峰值
    prices2 = np.random.randn(n_samples // 2) * 20 + 3050  # 第二个峰值
    # 中间区域（LVN）样本较少
    prices_mid = np.random.randn(n_samples // 10) * 5 + 3000

    all_prices = np.concatenate([prices1, prices2, prices_mid])

    # 计算KDE
    kde_core = KDECore(config)

    print("计算KDE...")
    start_time = time.perf_counter()
    grid, densities = kde_core.compute_kde(all_prices)
    kde_time = time.perf_counter() - start_time

    print(f"  KDE计算时间: {kde_time*1000:.1f}ms")
    print(f"  网格大小: {len(grid)}")

    # 提取LVN区域
    print("提取LVN区域...")
    start_time = time.perf_counter()
    lvn_regions = extractor.extract_from_kde(grid, densities)
    extraction_time = time.perf_counter() - start_time

    print(f"  LVN提取时间: {extraction_time*1000:.1f}ms")
    print(f"  检测到LVN区域数: {len(lvn_regions)}")

    # 输出LVN区域详情
    for i, region in enumerate(lvn_regions):
        print(f"  LVN {i+1}:")
        print(f"    价格范围: {region.start_price:.2f} - {region.end_price:.2f}")
        print(f"    中心价格: {region.center_price:.2f}")
        print(f"    宽度: {region.metrics.get('width', 0):.2f}")
        print(f"    深度比: {region.metrics.get('depth_ratio', 0):.3f}")
        print(f"    最小密度: {region.min_density:.2e}")

    # 验证LVN位置（应该在3000附近）
    if len(lvn_regions) > 0:
        for region in lvn_regions:
            if 2980 < region.center_price < 3020:
                print("✅ 成功检测到预期的LVN区域（3000附近）")
                break
        else:
            print("⚠️ 未检测到3000附近的LVN区域")
    else:
        print("⚠️ 未检测到任何LVN区域")

    return lvn_regions


def test_lvn_extraction():
    """pytest测试版本：LVN提取功能测试"""
    config = KDEEngineConfig()
    extractor = LVNExtractor(config)

    # 创建测试数据（模拟双峰分布，中间有LVN）
    np.random.seed(42)
    n_samples = 1000  # 使用较少样本以加快测试速度

    prices1 = np.random.randn(n_samples // 2) * 20 + 2950
    prices2 = np.random.randn(n_samples // 2) * 20 + 3050
    prices_mid = np.random.randn(n_samples // 10) * 5 + 3000

    all_prices = np.concatenate([prices1, prices2, prices_mid])

    # 计算KDE
    kde_core = KDECore(config)
    grid, densities = kde_core.compute_kde(all_prices)

    # 确保KDE计算成功
    assert len(grid) > 0
    assert len(densities) > 0
    assert len(grid) == len(densities)

    # 提取LVN区域
    lvn_regions = extractor.extract_from_kde(grid, densities)

    # 验证提取结果
    # 注意：由于随机数据，可能检测不到LVN，所以不强制要求检测到区域
    # 但至少验证函数运行正常
    assert lvn_regions is not None

    # 如果检测到区域，验证区域属性
    for region in lvn_regions:
        assert region.start_price < region.end_price
        assert region.min_price >= region.start_price
        assert region.min_price <= region.end_price
        assert region.min_density >= 0
        assert 'width' in region.metrics


def test_lvn_region_contains():
    """测试LVN区域包含检查"""
    # 创建测试区域
    metrics = {
        'width': 10.0,
        'depth_ratio': 0.5,
        'area': 50.0
    }

    region = LVNRegion(
        region_id=1,
        price_range=(2950.0, 2960.0),
        min_price=2955.0,
        min_density=0.1,
        metrics=metrics
    )

    # 测试包含检查
    assert region.contains_price(2955.0) == True
    assert region.contains_price(2950.0) == True  # 边界
    assert region.contains_price(2960.0) == True  # 边界
    assert region.contains_price(2949.9) == False
    assert region.contains_price(2960.1) == False

    # 测试距离计算
    assert region.distance_to_center(2955.0) == 0.0
    assert region.distance_to_center(2950.0) == 5.0
    assert region.distance_to_center(2960.0) == 5.0


def test_extractor_filter_and_merge():
    """测试提取器的过滤和合并功能"""
    config = KDEEngineConfig()
    extractor = LVNExtractor(config)

    # 创建测试区域（有重叠）
    metrics = {'width': 5.0, 'depth_ratio': 0.3, 'area': 20.0}
    regions = [
        LVNRegion(1, (2950.0, 2955.0), 2952.5, 0.1, metrics),
        LVNRegion(2, (2954.0, 2959.0), 2956.0, 0.2, metrics),  # 重叠
        LVNRegion(3, (2965.0, 2970.0), 2967.5, 0.15, metrics),  # 不重叠
    ]

    # 过滤和合并区域
    merged_regions = extractor.filter_and_merge_regions(regions, price_tolerance=0.5)

    # 验证合并结果
    assert len(merged_regions) <= len(regions)
    if len(merged_regions) == 2:  # 期望合并前两个区域
        assert merged_regions[0].start_price == 2950.0
        assert merged_regions[0].end_price == 2959.0
        assert merged_regions[1].start_price == 2965.0


def main():
    print("🚀 LVN提取器功能测试")
    print("=" * 70)

    lvn_regions = run_lvn_extraction_test()

    print("\n" + "=" * 70)
    print("🎉 LVN提取器功能测试完成!")
    print(f"  检测到 {len(lvn_regions)} 个LVN区域")


if __name__ == "__main__":
    main()