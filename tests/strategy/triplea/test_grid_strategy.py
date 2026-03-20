#!/usr/bin/env python3
"""
测试不同网格策略对KDE性能的影响
比较固定grid_size vs 固定grid_step策略
"""

import time
import numpy as np
import sys
sys.path.insert(0, '../../..')

from src.strategy.triplea.data_structures import KDEEngineConfig
from src.strategy.triplea.kde_core import KDECore, fast_kde_epanechnikov, silverman_bandwidth

class DynamicGridKDECore:
    """动态网格KDE核心（固定网格步长，限制最大网格点数）"""

    def __init__(self, config: KDEEngineConfig, grid_step: float = 0.1, max_grid_size: int = 100):
        self.config = config
        self.grid_step = grid_step  # 固定网格步长（ETH tick size通常为0.01）
        self.max_grid_size = max_grid_size
        self.cached_bandwidth: float = None

        # 预热Numba函数
        self._warmup_numba_functions()

    def _warmup_numba_functions(self):
        """预热Numba JIT编译的函数"""
        try:
            test_prices = np.random.randn(10) * 50 + 3000
            test_grid = np.linspace(2900, 3100, 20)
            test_densities = np.random.random(20)

            bandwidth = silverman_bandwidth(test_prices)
            fast_kde_epanechnikov(test_prices, test_grid, bandwidth)
        except Exception as e:
            print(f"预热异常: {e}")

    def compute_kde(self, prices: np.ndarray):
        """计算KDE密度估计（动态网格版本）"""
        if len(prices) < self.config.min_slice_ticks:
            return np.array([]), np.array([])

        # 计算带宽
        bandwidth = silverman_bandwidth(prices)

        # 创建动态网格
        grid = self._create_dynamic_grid(prices)

        if len(grid) == 0:
            return np.array([]), np.array([])

        # 计算密度估计
        densities = fast_kde_epanechnikov(prices, grid, bandwidth)

        return grid, densities

    def _create_dynamic_grid(self, prices: np.ndarray) -> np.ndarray:
        """创建动态评估网格（固定步长，限制最大点数）"""
        if len(prices) == 0:
            return np.array([])

        # 计算价格范围并扩展
        min_price = np.min(prices)
        max_price = np.max(prices)
        price_range = max_price - min_price

        # 扩展范围（10%）
        margin = price_range * 0.1
        grid_min = min_price - margin
        grid_max = max_price + margin

        # 计算需要的网格点数（基于固定步长）
        required_points = int((grid_max - grid_min) / self.grid_step) + 1

        # 限制最大网格点数
        n_points = min(required_points, self.max_grid_size)

        # 确保至少3个点
        n_points = max(n_points, 3)

        return np.linspace(grid_min, grid_max, n_points)

def generate_test_data(small_pulse: bool = True):
    """生成测试数据：小脉冲或大脉冲"""
    np.random.seed(42)

    if small_pulse:
        # 小脉冲：价格范围小（约10个tick）
        base_price = 3000.0
        price_range = 0.5  # 0.5美元范围，约50个tick（假设tick size=0.01）
        n_samples = 1000
        prices = np.random.randn(n_samples) * (price_range/2) + base_price
    else:
        # 大脉冲：价格范围大（约200个tick）
        base_price = 3000.0
        price_range = 20.0  # 20美元范围，约2000个tick
        n_samples = 1000
        prices = np.random.randn(n_samples) * (price_range/2) + base_price

    return prices

def _test_performance(strategy_name: str, kde_core, prices, n_runs: int = 100):
    """测试性能（内部函数，不是pytest测试）"""
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
    max_time = np.max(times)

    # 获取网格信息
    grid, densities = kde_core.compute_kde(prices)
    grid_size = len(grid) if len(grid) > 0 else 0

    return {
        'strategy': strategy_name,
        'avg_time_ms': avg_time,
        'min_time_ms': min_time,
        'max_time_ms': max_time,
        'grid_size': grid_size,
        'price_range': np.max(prices) - np.min(prices) if len(prices) > 0 else 0
    }

def _test_lvn_detection_consistency(strategy1, strategy2, prices):
    """测试LVN检测一致性（内部函数，不是pytest测试）"""
    from src.strategy.triplea.lvn_extractor import LVNExtractor

    config = KDEEngineConfig()
    extractor = LVNExtractor(config)

    # 使用策略1计算KDE
    grid1, densities1 = strategy1.compute_kde(prices)
    lvn_regions1 = extractor.extract_from_kde(grid1, densities1) if len(grid1) > 0 else []

    # 使用策略2计算KDE
    grid2, densities2 = strategy2.compute_kde(prices)
    lvn_regions2 = extractor.extract_from_kde(grid2, densities2) if len(grid2) > 0 else []

    # 比较LVN区域数量
    return {
        'strategy1_lvn_count': len(lvn_regions1),
        'strategy2_lvn_count': len(lvn_regions2),
        'grid1_size': len(grid1),
        'grid2_size': len(grid2),
        'grid1_step': (grid1[-1] - grid1[0]) / (len(grid1) - 1) if len(grid1) > 1 else 0,
        'grid2_step': (grid2[-1] - grid2[0]) / (len(grid2) - 1) if len(grid2) > 1 else 0
    }

def main():
    print("🔬 网格策略性能对比测试")
    print("=" * 70)

    config = KDEEngineConfig()

    # 创建三种策略
    fixed_50 = KDECore(config)  # 固定grid_size=50（当前策略）

    # 动态策略1：固定步长0.1（10个tick），最大100点
    dynamic_01_100 = DynamicGridKDECore(config, grid_step=0.1, max_grid_size=100)

    # 动态策略2：固定步长0.05（5个tick），最大150点
    dynamic_005_150 = DynamicGridKDECore(config, grid_step=0.05, max_grid_size=150)

    strategies = [
        ('固定grid_size=50', fixed_50),
        ('动态grid_step=0.1(max=100)', dynamic_01_100),
        ('动态grid_step=0.05(max=150)', dynamic_005_150)
    ]

    # 测试场景
    test_scenarios = [
        ('小脉冲场景', generate_test_data(small_pulse=True)),
        ('大脉冲场景', generate_test_data(small_pulse=False))
    ]

    all_results = []

    for scenario_name, prices in test_scenarios:
        print(f"\n📊 {scenario_name}")
        print(f"  价格范围: {np.max(prices) - np.min(prices):.4f}")
        print(f"  价格标准差: {np.std(prices):.4f}")
        print(f"  样本数量: {len(prices)}")
        print("-" * 50)

        scenario_results = []

        for strategy_name, strategy in strategies:
            result = _test_performance(strategy_name, strategy, prices, n_runs=50)
            scenario_results.append(result)

            print(f"  {strategy_name}:")
            print(f"    平均延迟: {result['avg_time_ms']:.3f}ms")
            print(f"    最小时延: {result['min_time_ms']:.3f}ms")
            print(f"    网格大小: {result['grid_size']}")
            if 'grid_step' in result:
                print(f"    网格步长: {result['grid_step']:.4f}")
            print()

        # 测试LVN检测一致性（比较固定策略和最佳动态策略）
        print("  🔄 LVN检测一致性测试:")
        consistency = _test_lvn_detection_consistency(fixed_50, dynamic_01_100, prices)
        print(f"    固定策略LVN数量: {consistency['strategy1_lvn_count']}")
        print(f"    动态策略LVN数量: {consistency['strategy2_lvn_count']}")
        print(f"    固定策略网格步长: {consistency['grid1_step']:.4f}")
        print(f"    动态策略网格步长: {consistency['grid2_step']:.4f}")

        all_results.append((scenario_name, scenario_results))

    # 总结分析
    print("\n" + "=" * 70)
    print("📈 总结分析")
    print("=" * 70)

    for scenario_name, scenario_results in all_results:
        print(f"\n{scenario_name}:")

        # 找到最佳性能策略
        best_perf = min(scenario_results, key=lambda x: x['avg_time_ms'])
        print(f"  最佳性能: {best_perf['strategy']} ({best_perf['avg_time_ms']:.3f}ms)")

        # 找到最细网格策略
        finest_grid = max(scenario_results, key=lambda x: x['grid_size'])
        print(f"  最细网格: {finest_grid['strategy']} ({finest_grid['grid_size']}点)")

    print("\n💡 建议:")
    print("  1. 如果性能是首要考虑，保持固定grid_size=50")
    print("  2. 如果需要更精细的分辨率，考虑动态网格策略")
    print("  3. 动态策略在价格范围变化大时能保持更一致的步长")

if __name__ == "__main__":
    main()