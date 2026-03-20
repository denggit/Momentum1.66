#!/usr/bin/env python3
"""
测试LVN管理器基本功能
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
from src.strategy.triplea.lvn_manager import LVNManager, LVNCluster

def run_basic_functionality_test():
    """运行基本功能测试（脚本模式）"""
    print("🔬 测试LVN管理器基本功能")
    print("-" * 60)

    # 创建配置
    config = KDEEngineConfig()
    manager = LVNManager(config)

    print(f"管理器初始化完成: {manager}")

    # 测试空KDE结果
    empty_grid = np.array([])
    empty_densities = np.array([])

    clusters = manager.process_kde_result(empty_grid, empty_densities)
    print(f"空输入处理: {len(clusters)} 个簇")

    # 创建测试KDE数据（模拟真实检测）
    test_grid = np.linspace(2950, 3050, 100)

    # 在2980和3020附近创建低密度区域
    test_densities = np.ones(100) * 50
    test_densities[30:35] = 10  # 2980附近低密度
    test_densities[65:70] = 15  # 3020附近低密度

    # 第一次检测
    clusters1 = manager.process_kde_result(test_grid, test_densities)
    print(f"第一次检测: {len(clusters1)} 个簇")

    for i, cluster in enumerate(clusters1):
        print(f"  簇 {i}: ID={cluster.cluster_id}, 区域数={len(cluster.regions)}")
        print(f"    范围: [{cluster.merged_start_price:.2f}, {cluster.merged_end_price:.2f}]")
        print(f"    最小价格: {cluster.merged_min_price:.2f}")
        print(f"    置信度: {cluster.confidence:.2f}")

    # 稍微修改数据，模拟第二次检测（部分区域重叠）
    test_densities2 = test_densities.copy()
    test_densities2[32:37] = 12  # 稍微移动低密度区域

    # 第二次检测
    print(f"\n第二次检测 (部分区域重叠):")
    clusters2 = manager.process_kde_result(test_grid, test_densities2)
    print(f"  检测后簇数: {len(clusters2)}")

    # 检查合并统计
    stats = manager.get_statistics()
    print(f"\n统计信息:")
    print(f"  总区域检测数: {stats['total_regions_detected']}")
    print(f"  区域合并数: {stats['regions_merged']}")
    print(f"  簇创建数: {stats['clusters_created']}")
    print(f"  活跃簇数: {stats['active_clusters_count']}")

    return manager

def test_basic_functionality():
    """pytest测试版本：基本功能测试"""
    config = KDEEngineConfig()
    manager = LVNManager(config)

    # 测试空KDE结果
    empty_grid = np.array([])
    empty_densities = np.array([])
    clusters = manager.process_kde_result(empty_grid, empty_densities)
    assert len(clusters) == 0

    # 创建测试KDE数据（模拟真实检测）
    test_grid = np.linspace(2950, 3050, 100)
    test_densities = np.ones(100) * 50
    test_densities[30:35] = 10  # 2980附近低密度
    test_densities[65:70] = 15  # 3020附近低密度

    # 第一次检测
    clusters1 = manager.process_kde_result(test_grid, test_densities)
    assert len(clusters1) > 0

    # 第二次检测（部分区域重叠）
    test_densities2 = test_densities.copy()
    test_densities2[32:37] = 12
    clusters2 = manager.process_kde_result(test_grid, test_densities2)

    # 检查统计信息
    stats = manager.get_statistics()
    assert stats['total_regions_detected'] > 0
    assert stats['clusters_created'] > 0

def test_cluster_operations():
    """测试簇操作"""
    print("\n🔧 测试簇操作")
    print("-" * 60)

    # 创建测试簇
    cluster = LVNCluster(
        cluster_id=1,
        regions=[]
    )

    print(f"创建簇: {cluster}")

    # 测试簇方法
    cluster.update_merged_attributes()
    print(f"更新后: {cluster}")

    metrics = cluster.get_cluster_metrics()
    print(f"簇度量: {metrics}")

def test_closest_cluster():
    """测试最近簇查找"""
    print("\n🎯 测试最近簇查找")
    print("-" * 60)

    # 创建管理器
    config = KDEEngineConfig()
    manager = LVNManager(config)

    # 创建测试数据
    test_grid = np.linspace(2950, 3050, 100)
    test_densities = np.random.random(100) * 100

    # 在特定位置创建低密度区域
    test_densities[40:45] = 5   # 2980附近
    test_densities[70:75] = 8   # 3020附近

    # 处理检测
    manager.process_kde_result(test_grid, test_densities)

    # 测试不同价格的最近簇
    test_prices = [2970, 3000, 3030, 3100]

    print("最近簇查找测试:")
    for price in test_prices:
        cluster = manager.find_closest_cluster(price, max_distance=100)
        if cluster:
            distance = abs(price - cluster.merged_min_price)
            print(f"  价格 {price}: 簇ID={cluster.cluster_id}, "
                  f"最小价格={cluster.merged_min_price:.2f}, "
                  f"距离={distance:.2f}")
        else:
            print(f"  价格 {price}: 无最近簇")

def test_confidence_update():
    """测试置信度更新"""
    print("\n📊 测试置信度更新")
    print("-" * 60)

    config = KDEEngineConfig()
    manager = LVNManager(config)

    # 创建测试数据
    test_grid = np.linspace(2950, 3050, 100)
    test_densities = np.random.random(100) * 100
    test_densities[50:55] = 10  # 3000附近低密度

    clusters = manager.process_kde_result(test_grid, test_densities)

    if clusters:
        cluster = clusters[0]
        initial_confidence = cluster.confidence

        # 模拟价格在区域内停留
        price_action_data = {
            'time_in_region': 600,  # 10分钟
            'oscillations_around_region': 5
        }

        new_confidence = manager.update_cluster_confidence(
            cluster.cluster_id, price_action_data
        )

        print(f"置信度更新: {initial_confidence:.3f} -> {new_confidence:.3f}")

def main():
    print("🚀 LVN管理器基本功能测试")
    print("=" * 70)

    manager = run_basic_functionality_test()
    test_cluster_operations()
    test_closest_cluster()
    test_confidence_update()

    print("\n" + "=" * 70)
    print("🎉 所有基本功能测试完成!")

    # 打印最终统计
    stats = manager.get_statistics()
    print(f"\n📈 最终统计:")
    for key, value in stats.items():
        print(f"  {key}: {value}")

if __name__ == "__main__":
    main()