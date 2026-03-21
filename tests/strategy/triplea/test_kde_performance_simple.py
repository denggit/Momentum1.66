#!/usr/bin/env python3
"""
简化版KDE性能测试
验证阶段5开发的KDE引擎性能
"""

import os
import sys
import time

import numpy as np

# 获取项目根目录并添加到路径
# 文件位置: tests/strategy/triplea/test_kde_performance_simple.py
# 需要向上三级才能到达项目根目录
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

print('🚀 开始KDE引擎性能测试...')

# 导入模块
try:
    from src.strategy.triplea.data_structures import TripleAEngineConfig
    from src.strategy.triplea.kde_core import KDECore
    from src.strategy.triplea.kde_matrix import KDEMatrixEngine
    from src.strategy.triplea.lvn_extractor import LVNExtractor

    print('✅ 模块导入成功')
except ImportError as e:
    print(f'❌ 模块导入失败: {e}')
    print(f'  项目根目录: {project_root}')
    print(f'  sys.path: {sys.path}')
    sys.exit(1)

# 创建配置
config = TripleAEngineConfig()

# 创建测试数据
np.random.seed(42)
n_samples = 1000
prices = np.random.randn(n_samples) * 50 + 3000

print(f'📊 测试配置:')
print(f'  样本数量: {n_samples}')
print(f'  价格范围: {np.min(prices):.2f} - {np.max(prices):.2f}')

# 1. 测试KDE核心计算
print('\n1. 测试KDE核心计算...')
kde_core = KDECore(config.kde_engine)

latencies = []
for i in range(10):
    start_time = time.perf_counter_ns()
    grid, densities = kde_core.compute_kde(prices)
    end_time = time.perf_counter_ns()
    latency_ms = (end_time - start_time) / 1_000_000
    latencies.append(latency_ms)

    if i == 0:
        print(f'   首次运行: {latency_ms:.3f}ms, 网格大小: {len(grid)}')

avg_kde_latency = np.mean(latencies)
kde_passed = avg_kde_latency < 0.2
print(f'  平均延迟: {avg_kde_latency:.3f}ms (目标: <0.2ms)')
print(f'  {"✅ 通过" if kde_passed else "❌ 失败"}')

# 2. 测试LVN提取
print('\n2. 测试LVN提取...')
if len(grid) > 0 and len(densities) > 0:
    lvn_extractor = LVNExtractor(config.kde_engine)

    # 预热
    for _ in range(3):
        lvn_extractor.extract_from_kde(grid, densities)

    latencies = []
    regions_list = []
    for i in range(10):
        start_time = time.perf_counter_ns()
        regions = lvn_extractor.extract_from_kde(grid, densities)
        end_time = time.perf_counter_ns()
        latency_ms = (end_time - start_time) / 1_000_000
        latencies.append(latency_ms)
        regions_list.append(regions)

        if i == 0:
            print(f'   首次运行: {latency_ms:.3f}ms, 检测到LVN区域: {len(regions)}')

    avg_lvn_latency = np.mean(latencies)
    lvn_passed = avg_lvn_latency < 0.1
    print(f'  平均延迟: {avg_lvn_latency:.3f}ms (目标: <0.1ms)')
    print(f'  {"✅ 通过" if lvn_passed else "❌ 失败"}')

    # 显示LVN区域详情
    if regions_list[0]:
        print(f'  LVN区域详情:')
        for i, region in enumerate(regions_list[0][:3]):  # 只显示前3个
            print(
                f'    区域{i + 1}: {region.start_price:.2f} - {region.end_price:.2f}, 中心: {region.center_price:.2f}')
else:
    print('  ❌ KDE计算失败，跳过LVN提取')
    lvn_passed = False
    regions_list = [[]]

# 3. 测试批量KDE计算
print('\n3. 测试批量KDE计算...')
kde_matrix = KDEMatrixEngine(config.kde_engine)

# 创建批量数据
n_batches = 5
batch_size = 200
price_batches = [prices[i * batch_size:(i + 1) * batch_size] for i in range(n_batches)]

start_time = time.perf_counter_ns()
results = kde_matrix.compute_batch_kde(price_batches)
end_time = time.perf_counter_ns()

total_time_s = (end_time - start_time) / 1_000_000_000
total_samples = sum(len(batch) for batch in price_batches)
throughput = total_samples / total_time_s if total_time_s > 0 else 0

batch_passed = throughput > 10000
print(f'  批次数量: {n_batches}')
print(f'  批次大小: {batch_size}')
print(f'  总样本数: {total_samples}')
print(f'  总时间: {total_time_s:.3f}s')
print(f'  吞吐量: {throughput:.0f} 样本/秒 (目标: >10,000)')
print(f'  {"✅ 通过" if batch_passed else "❌ 失败"}')

# 4. 测试完整流程
print('\n4. 测试完整KDE+LVN流程...')
start_time = time.perf_counter_ns()

# KDE计算
grid, densities = kde_core.compute_kde(prices)

# LVN提取
if len(grid) > 0 and len(densities) > 0:
    lvn_extractor = LVNExtractor(config.kde_engine)
    regions = lvn_extractor.extract_from_kde(grid, densities)
    end_time = time.perf_counter_ns()

    total_time_ms = (end_time - start_time) / 1_000_000
    full_passed = total_time_ms < 0.5
    print(f'  总时间: {total_time_ms:.3f}ms (目标: <0.5ms)')
    print(f'  检测到LVN区域: {len(regions)}')
    print(f'  {"✅ 通过" if full_passed else "❌ 失败"}')
else:
    print('  ❌ KDE计算失败')
    full_passed = False

# 5. 测试正确性
print('\n5. 测试算法正确性...')
try:
    # 验证KDE输出
    assert len(grid) > 0, "KDE网格为空"
    assert len(densities) > 0, "KDE密度为空"
    assert len(grid) == len(densities), "网格和密度长度不匹配"
    assert np.all(densities >= 0), "密度包含负值"
    assert np.all(np.diff(grid) > 0), "网格不是单调递增"

    # 验证LVN区域
    if regions_list[0]:
        for region in regions_list[0]:
            assert region.start_price < region.end_price, "LVN区域起始价格大于结束价格"
            assert region.min_density >= 0, "LVN最小密度为负"
            assert region.start_price <= region.min_price <= region.end_price, "最小价格不在区域范围内"

    print(f'  ✅ 正确性验证通过')
    correctness_passed = True
except AssertionError as e:
    print(f'  ❌ 正确性验证失败: {e}')
    correctness_passed = False

# 总结
print('\n' + '=' * 60)
print('📈 性能测试总结')
print('=' * 60)
print(f'  KDE核心计算: {"✅ 通过" if kde_passed else "❌ 失败"} ({avg_kde_latency:.3f}ms)')
print(
    f'  LVN提取: {"✅ 通过" if lvn_passed else "❌ 失败"} ({avg_lvn_latency if "avg_lvn_latency" in locals() else "N/A":.3f}ms)')
print(f'  批量吞吐量: {"✅ 通过" if batch_passed else "❌ 失败"} ({throughput:.0f} 样本/秒)')
print(
    f'  完整流程: {"✅ 通过" if full_passed else "❌ 失败"} ({total_time_ms if "total_time_ms" in locals() else "N/A":.3f}ms)')
print(f'  正确性: {"✅ 通过" if correctness_passed else "❌ 失败"}')

all_passed = kde_passed and lvn_passed and batch_passed and full_passed and correctness_passed
if all_passed:
    print('\n🎉 所有测试通过！阶段5开发完成！')
else:
    print('\n⚠️  部分测试未通过，需要优化')

print('=' * 60)
