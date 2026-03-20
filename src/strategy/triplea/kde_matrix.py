"""
四号引擎v3.0 KDE矩阵计算库
基于Numpy广播和Numba加速的高性能KDE计算
支持批量处理和实时分析
"""

import math
from typing import Tuple, List

import numpy as np
from numba import njit, prange

from src.strategy.triplea.data_structures import KDEEngineConfig
from src.utils.log import get_logger

logger = get_logger(__name__)


@njit(cache=True, fastmath=True, parallel=True)
def matrix_broadcast_kde_numba(
        prices: np.ndarray,
        grid: np.ndarray,
        bandwidth: float = 0.5
) -> np.ndarray:
    """
    Numba加速的矩阵广播KDE计算（并行版本）

    Args:
        prices: 价格数组 (n_samples,)
        grid: 评估网格数组 (n_grid,)
        bandwidth: 带宽参数

    Returns:
        密度估计数组 (n_grid,)
    """
    n_samples = len(prices)
    n_grid = len(grid)

    if n_samples == 0 or n_grid == 0:
        return np.zeros(0)

    # 预计算常数
    norm_factor = 1.0 / (n_samples * bandwidth * math.sqrt(2 * math.pi))
    bandwidth_sq = bandwidth * bandwidth

    # 结果数组
    kde_values = np.zeros(n_grid)

    # 并行计算每个网格点
    for i in prange(n_grid):
        grid_val = grid[i]
        density_sum = 0.0

        # 累加所有样本点的贡献
        for j in range(n_samples):
            diff = prices[j] - grid_val
            exponent = -0.5 * diff * diff / bandwidth_sq
            density_sum += math.exp(exponent)

        kde_values[i] = density_sum * norm_factor

    return kde_values


def matrix_broadcast_kde_python(
        prices: np.ndarray,
        grid: np.ndarray,
        bandwidth: float = 0.5
) -> np.ndarray:
    """
    Python版本矩阵广播KDE计算（用于调试和验证）

    Args:
        prices: 价格数组 (n_samples,)
        grid: 评估网格数组 (n_grid,)
        bandwidth: 带宽参数

    Returns:
        密度估计数组 (n_grid,)
    """
    n_samples = len(prices)
    n_grid = len(grid)

    if n_samples == 0 or n_grid == 0:
        return np.array([])

    # 使用Numpy广播计算差异矩阵
    # prices: (n_samples, 1), grid: (1, n_grid)
    # diff: (n_samples, n_grid)
    diff = prices[:, np.newaxis] - grid[np.newaxis, :]

    # 计算高斯核
    kernel = np.exp(-0.5 * (diff / bandwidth) ** 2)

    # 沿样本轴求和并归一化
    kde_values = np.sum(kernel, axis=0) / (n_samples * bandwidth * math.sqrt(2 * np.pi))

    return kde_values


def kde_batch_matrix(
        price_batches: List[np.ndarray],
        grid: np.ndarray,
        bandwidth: float = 0.5,
        use_numba: bool = True
) -> List[np.ndarray]:
    """
    批量计算KDE（支持多个价格序列）

    Args:
        price_batches: 价格序列列表
        grid: 统一的评估网格
        bandwidth: 带宽参数
        use_numba: 是否使用Numba加速

    Returns:
        KDE结果列表
    """
    results = []

    if use_numba:
        kde_func = matrix_broadcast_kde_numba
    else:
        kde_func = matrix_broadcast_kde_python

    for prices in price_batches:
        if len(prices) == 0:
            results.append(np.array([]))
            continue

        kde_values = kde_func(prices, grid, bandwidth)
        results.append(kde_values)

    return results


@njit(cache=True, fastmath=True)
def adaptive_bandwidth_matrix(
        prices: np.ndarray,
        grid: np.ndarray,
        base_bandwidth: float,
        sensitivity: float = 0.5
) -> np.ndarray:
    """
    自适应带宽计算（根据局部密度调整带宽）

    Args:
        prices: 价格数组
        grid: 评估网格
        base_bandwidth: 基础带宽
        sensitivity: 敏感度参数 (0-1)

    Returns:
        自适应带宽数组 (n_grid,)
    """
    n_grid = len(grid)
    adaptive_bandwidths = np.zeros(n_grid)

    for i in range(n_grid):
        # 计算当前网格点附近的样本密度
        local_density = 0.0
        grid_val = grid[i]

        for price in prices:
            distance = abs(price - grid_val)
            if distance < base_bandwidth * 2:
                local_density += 1.0

        # 根据局部密度调整带宽
        # 密度越高，带宽越小（提高分辨率）
        # 密度越低，带宽越大（平滑）
        density_factor = max(0.1, min(2.0, 1.0 / (local_density + 1.0)))
        adaptive_bandwidths[i] = base_bandwidth * density_factor * sensitivity

    return adaptive_bandwidths


@njit(cache=True, fastmath=True, parallel=True)
def compute_multiple_kde_parallel(
        price_matrices: List[np.ndarray],
        grids: List[np.ndarray],
        bandwidths: np.ndarray
) -> List[np.ndarray]:
    """
    并行计算多个KDE估计

    Args:
        price_matrices: 价格矩阵列表
        grids: 网格列表
        bandwidths: 带宽数组

    Returns:
        KDE结果列表
    """
    n_tasks = len(price_matrices)
    results = [np.zeros(0) for _ in range(n_tasks)]

    # 并行处理每个任务
    for task_idx in prange(n_tasks):
        prices = price_matrices[task_idx]
        grid = grids[task_idx]
        bandwidth = bandwidths[task_idx]

        if len(prices) == 0 or len(grid) == 0:
            continue

        n_samples = len(prices)
        n_grid = len(grid)

        norm_factor = 1.0 / (n_samples * bandwidth * math.sqrt(2 * math.pi))
        bandwidth_sq = bandwidth * bandwidth

        kde_values = np.zeros(n_grid)

        for i in range(n_grid):
            grid_val = grid[i]
            density_sum = 0.0

            for j in range(n_samples):
                diff = prices[j] - grid_val
                exponent = -0.5 * diff * diff / bandwidth_sq
                density_sum += math.exp(exponent)

            kde_values[i] = density_sum * norm_factor

        results[task_idx] = kde_values

    return results


class KDEMatrixEngine:
    """
    KDE矩阵计算引擎
    支持批量处理和并行计算
    """

    def __init__(self, config: KDEEngineConfig):
        """
        初始化KDE矩阵计算引擎

        Args:
            config: KDE引擎配置
        """
        self.config = config
        self.default_grid_size = 200

        logger.info(f"KDEMatrixEngine初始化完成")

    def create_unified_grid(
            self,
            all_prices: List[np.ndarray],
            margin_pct: float = 0.1
    ) -> np.ndarray:
        """
        创建统一的评估网格（覆盖所有价格范围）

        Args:
            all_prices: 所有价格序列列表
            margin_pct: 边缘扩展百分比

        Returns:
            统一的评估网格
        """
        # 计算全局价格范围
        all_prices_flat = np.concatenate(all_prices) if len(all_prices) > 0 else np.array([])

        if len(all_prices_flat) == 0:
            return np.array([])

        global_min = np.min(all_prices_flat)
        global_max = np.max(all_prices_flat)
        price_range = global_max - global_min

        # 扩展范围
        margin = price_range * margin_pct
        grid_min = global_min - margin
        grid_max = global_max + margin

        # 创建均匀网格
        n_points = min(self.default_grid_size, len(all_prices_flat))
        return np.linspace(grid_min, grid_max, n_points)

    def compute_batch_kde(
            self,
            price_batches: List[np.ndarray],
            use_adaptive_bandwidth: bool = False
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        批量计算KDE（多序列并行处理）

        Args:
            price_batches: 价格序列列表
            use_adaptive_bandwidth: 是否使用自适应带宽

        Returns:
            (网格, 密度) 元组列表
        """
        results = []

        # 如果有多个序列，使用统一的网格
        unified_grid = self.create_unified_grid(price_batches)

        for prices in price_batches:
            if len(prices) < self.config.min_slice_ticks:
                logger.debug(f"数据点不足，跳过: {len(prices)} < {self.config.min_slice_ticks}")
                results.append((np.array([]), np.array([])))
                continue

            # 计算带宽
            from src.strategy.triplea.kde_core import silverman_bandwidth
            base_bandwidth = silverman_bandwidth(prices)

            if use_adaptive_bandwidth:
                # 计算自适应带宽数组
                adaptive_bw = adaptive_bandwidth_matrix(prices, unified_grid, base_bandwidth)
                # 对于自适应带宽，需要分别计算每个网格点的密度
                # 这里简化处理，使用平均带宽
                bandwidth = np.mean(adaptive_bw)
            else:
                bandwidth = base_bandwidth

            # 计算KDE
            densities = matrix_broadcast_kde_numba(prices, unified_grid, bandwidth)

            results.append((unified_grid, densities))

        return results

    def compute_density_heatmap(
            self,
            price_batches: List[np.ndarray],
            grid_points: int = 100,
            time_points: int = 50
    ) -> np.ndarray:
        """
        计算密度热图（时间-价格密度分布）

        Args:
            price_batches: 按时间顺序的价格序列列表
            grid_points: 价格轴分辨率
            time_points: 时间轴分辨率

        Returns:
            密度热图矩阵 (time_points, grid_points)
        """
        if len(price_batches) == 0:
            return np.zeros((time_points, grid_points))

        # 创建统一的网格
        unified_grid = self.create_unified_grid(price_batches)

        # 如果时间点太多，进行采样
        n_time_slices = len(price_batches)
        if n_time_slices > time_points:
            # 均匀采样
            indices = np.linspace(0, n_time_slices - 1, time_points, dtype=int)
            sampled_batches = [price_batches[i] for i in indices]
        else:
            sampled_batches = price_batches
            time_points = n_time_slices

        # 计算每个时间片的KDE
        heatmap = np.zeros((time_points, len(unified_grid)))

        for t_idx, prices in enumerate(sampled_batches):
            if len(prices) < self.config.min_slice_ticks:
                continue

            from src.strategy.triplea.kde_core import silverman_bandwidth
            bandwidth = silverman_bandwidth(prices)

            densities = matrix_broadcast_kde_numba(prices, unified_grid, bandwidth)
            heatmap[t_idx, :] = densities

        return heatmap


# 性能测试
def test_kde_matrix_performance():
    """测试KDE矩阵计算性能"""
    import time

    logger = get_logger(__name__)

    # 创建测试数据
    n_batches = 10
    n_samples_per_batch = 5000
    price_batches = []

    for i in range(n_batches):
        prices = 3000 + np.random.randn(n_samples_per_batch) * 50
        price_batches.append(prices)

    # 创建配置
    config = KDEEngineConfig()
    engine = KDEMatrixEngine(config)

    # 性能测试
    logger.info(f"🔬 KDE矩阵性能测试 (n_batches={n_batches}, n_samples={n_samples_per_batch})")

    # 测试批量计算
    start_time = time.perf_counter()
    results = engine.compute_batch_kde(price_batches)
    batch_time = time.perf_counter() - start_time

    logger.info(f"  批量计算时间: {batch_time * 1000:.1f}ms")
    logger.info(f"  平均单序列时间: {batch_time / n_batches * 1000:.1f}ms")

    # 测试热图计算
    start_time = time.perf_counter()
    heatmap = engine.compute_density_heatmap(price_batches[:5])  # 只使用前5个
    heatmap_time = time.perf_counter() - start_time

    logger.info(f"  热图计算时间: {heatmap_time * 1000:.1f}ms")
    logger.info(f"  热图形状: {heatmap.shape}")

    return batch_time, heatmap_time


if __name__ == "__main__":
    # 运行性能测试
    test_kde_matrix_performance()
