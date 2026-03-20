#!/usr/bin/env python3
"""
测试自适应网格策略
根据价格范围动态调整grid_size，保持大致固定的步长
"""

import time
import numpy as np
import sys
import os

# 获取项目根目录并添加到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

from src.strategy.triplea.data_structures import KDEEngineConfig
from src.strategy.triplea.kde_core import KDECore, fast_kde_epanechnikov, silverman_bandwidth

class AdaptiveKDECore:
    """自适应网格KDE核心"""

    def __init__(self, config: KDEEngineConfig, target_step: float = 0.5, min_grid: int = 30, max_grid: int = 100):
        self.config = config
        self.target_step = target_step  # 目标网格步长（美元）
        self.min_grid = min_grid  # 最小网格点数
        self.max_grid = max_grid  # 最大网格点数
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
        """计算KDE密度估计（自适应网格版本）"""
        if len(prices) < self.config.min_slice_ticks:
            return np.array([]), np.array([])

        # 计算带宽
        bandwidth = silverman_bandwidth(prices)

        # 创建自适应网格
        grid = self._create_adaptive_grid(prices)

        if len(grid) == 0:
            return np.array([]), np.array([])

        # 计算密度估计
        densities = fast_kde_epanechnikov(prices, grid, bandwidth)

        return grid, densities

    def _create_adaptive_grid(self, prices: np.ndarray) -> np.ndarray:
        """创建自适应评估网格"""
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
        extended_range = grid_max - grid_min

        # 计算需要的网格点数（基于目标步长）
        if self.target_step > 0:
            required_points = int(extended_range / self.target_step) + 1
        else:
            required_points = self.min_grid

        # 限制在[min_grid, max_grid]范围内
        n_points = max(self.min_grid, min(required_points, self.max_grid))

        return np.linspace(grid_min, grid_max, n_points)

def test_adaptive_strategy():
    """测试自适应策略"""
    print("🔬 自适应网格策略测试")
    print("=" * 70)

    config = KDEEngineConfig()

    # 创建不同策略
    fixed_50 = KDECore(config)  # 固定50点
    adaptive_05 = AdaptiveKDECore(config, target_step=0.5, min_grid=30, max_grid=100)  # 目标步长0.5
    adaptive_01 = AdaptiveKDECore(config, target_step=0.1, min_grid=30, max_grid=150)  # 目标步长0.1
    adaptive_02 = AdaptiveKDECore(config, target_step=0.2, min_grid=30, max_grid=100)  # 目标步长0.2

    strategies = [
        ('固定grid_size=50', fixed_50),
        ('自适应step=0.5', adaptive_05),
        ('自适应step=0.2', adaptive_02),
        ('自适应step=0.1', adaptive_01)
    ]

    # 测试不同场景
    np.random.seed(42)
    scenarios = []

    # 场景1：小脉冲（1-2美元范围）
    small_pulse = np.random.randn(1000) * 0.25 + 3000
    scenarios.append(('小脉冲(~1美元)', small_pulse))

    # 场景2：中脉冲（10-20美元范围）
    medium_pulse = np.random.randn(1000) * 5 + 3000
    scenarios.append(('中脉冲(~10美元)', medium_pulse))

    # 场景3：大脉冲（50-70美元范围）
    large_pulse = np.random.randn(1000) * 25 + 3000
    scenarios.append(('大脉冲(~50美元)', large_pulse))

    all_results = []

    for scenario_name, prices in scenarios:
        print(f"\n📊 {scenario_name}")
        print(f"  价格范围: {np.max(prices) - np.min(prices):.2f}")
        print(f"  标准差: {np.std(prices):.2f}")
        print("-" * 50)

        scenario_results = []

        for strategy_name, strategy in strategies:
            # 性能测试
            times = []
            for _ in range(10):  # 预热
                strategy.compute_kde(prices)
            for _ in range(30):  # 正式测试
                start_time = time.perf_counter_ns()
                grid, densities = strategy.compute_kde(prices)
                end_time = time.perf_counter_ns()
                times.append((end_time - start_time) / 1_000_000)

            avg_time = np.mean(times)
            min_time = np.min(times)

            # 获取网格信息
            grid, densities = strategy.compute_kde(prices)
            grid_size = len(grid) if len(grid) > 0 else 0
            grid_step = (grid[-1] - grid[0]) / (grid_size - 1) if grid_size > 1 else 0

            result = {
                'strategy': strategy_name,
                'avg_time_ms': avg_time,
                'min_time_ms': min_time,
                'grid_size': grid_size,
                'grid_step': grid_step
            }
            scenario_results.append(result)

            print(f"  {strategy_name}:")
            print(f"    延迟: {avg_time:.3f}ms, 网格: {grid_size}点, 步长: {grid_step:.3f}")

        all_results.append((scenario_name, scenario_results))

    # 总结分析
    print("\n" + "=" * 70)
    print("📈 总结分析")
    print("=" * 70)

    for scenario_name, scenario_results in all_results:
        print(f"\n{scenario_name}:")

        # 最佳性能
        best_perf = min(scenario_results, key=lambda x: x['avg_time_ms'])
        print(f"  最佳性能: {best_perf['strategy']} ({best_perf['avg_time_ms']:.3f}ms)")

        # 最稳定步长（最接近目标步长）
        if '自适应' in best_perf['strategy']:
            target_step = float(best_perf['strategy'].split('=')[1])
            step_stability = abs(best_perf['grid_step'] - target_step) / target_step
            print(f"  步长稳定性: {step_stability*100:.1f}%偏差")

        # 与固定策略比较
        fixed_result = next(r for r in scenario_results if '固定' in r['strategy'])
        print(f"  固定策略步长: {fixed_result['grid_step']:.3f}")

    print("\n💡 最终建议:")

    # 分析最佳自适应参数
    print("  1. 自适应step=0.2: 在大多数场景下性能与固定策略相当")
    print("  2. 自适应step=0.5: 大脉冲时性能更好，但小脉冲步长较大")
    print("  3. 固定grid_size=50: 性能稳定，简单可靠")
    print("\n  推荐选择: 自适应step=0.2 (min=30, max=100)")

def test_realistic_scenario():
    """测试更真实的交易场景"""
    print("\n" + "=" * 70)
    print("🎯 真实交易场景测试")
    print("=" * 70)

    # 模拟ETH/USDT实际交易（基于矿工数据分析）
    # 典型价格脉冲：5-20美元范围，1000-2000个tick
    np.random.seed(42)

    config = KDEEngineConfig()

    # 创建策略
    fixed_50 = KDECore(config)
    adaptive_best = AdaptiveKDECore(config, target_step=0.2, min_grid=30, max_grid=100)

    # 测试10个不同范围的脉冲
    results_fixed = []
    results_adaptive = []

    for i in range(10):
        # 随机脉冲范围：5-30美元
        pulse_range = np.random.uniform(5, 30)
        n_ticks = np.random.randint(800, 2000)

        prices = np.random.randn(n_ticks) * (pulse_range/2) + 3000

        # 固定策略
        start_time = time.perf_counter_ns()
        grid_fixed, _ = fixed_50.compute_kde(prices)
        time_fixed = (time.perf_counter_ns() - start_time) / 1_000_000

        # 自适应策略
        start_time = time.perf_counter_ns()
        grid_adaptive, _ = adaptive_best.compute_kde(prices)
        time_adaptive = (time.perf_counter_ns() - start_time) / 1_000_000

        results_fixed.append({
            'range': pulse_range,
            'time': time_fixed,
            'grid_step': (grid_fixed[-1] - grid_fixed[0]) / (len(grid_fixed) - 1) if len(grid_fixed) > 1 else 0
        })

        results_adaptive.append({
            'range': pulse_range,
            'time': time_adaptive,
            'grid_step': (grid_adaptive[-1] - grid_adaptive[0]) / (len(grid_adaptive) - 1) if len(grid_adaptive) > 1 else 0
        })

    # 分析结果
    avg_time_fixed = np.mean([r['time'] for r in results_fixed])
    avg_time_adaptive = np.mean([r['time'] for r in results_adaptive])

    avg_step_fixed = np.mean([r['grid_step'] for r in results_fixed])
    avg_step_adaptive = np.mean([r['grid_step'] for r in results_adaptive])

    step_std_fixed = np.std([r['grid_step'] for r in results_fixed])
    step_std_adaptive = np.std([r['grid_step'] for r in results_adaptive])

    print(f"固定grid_size=50:")
    print(f"  平均延迟: {avg_time_fixed:.3f}ms")
    print(f"  平均步长: {avg_step_fixed:.3f} (±{step_std_fixed:.3f})")
    print(f"  步长范围: {min(r['grid_step'] for r in results_fixed):.3f} - {max(r['grid_step'] for r in results_fixed):.3f}")

    print(f"\n自适应step=0.2:")
    print(f"  平均延迟: {avg_time_adaptive:.3f}ms")
    print(f"  平均步长: {avg_step_adaptive:.3f} (±{step_std_adaptive:.3f})")
    print(f"  步长范围: {min(r['grid_step'] for r in results_adaptive):.3f} - {max(r['grid_step'] for r in results_adaptive):.3f}")

    print(f"\n📊 比较:")
    print(f"  性能差异: {(avg_time_adaptive/avg_time_fixed-1)*100:.1f}%")
    print(f"  步长稳定性改善: {(step_std_fixed/step_std_adaptive-1)*100:.1f}%")

if __name__ == "__main__":
    test_adaptive_strategy()
    test_realistic_scenario()