#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四号引擎v3.0 KDE核心函数库
基于Numba JIT编译的高性能核密度估计（KDE）实现
支持实时脉冲波分析和LVN检测
"""

import math
from typing import Tuple, Optional, List, Dict
import os
os.environ['NUMBA_DISABLE_CONFIG_FILE'] = '1'
os.environ['NUMBA_CONFIG_FILE'] = ''

import numpy as np
from numba import njit, prange

from src.strategy.triplea.core.data_structures import KDEEngineConfig
from src.utils.log import get_logger

logger = get_logger(__name__)


@njit(cache=True, fastmath=True)
def gaussian_kernel(x: float, bandwidth: float) -> float:
    """
    高斯核函数（Numba加速）

    Args:
        x: 标准化距离
        bandwidth: 带宽参数

    Returns:
        核函数值
    """
    # 高斯核公式: exp(-0.5 * (x / bandwidth)^2) / (bandwidth * sqrt(2 * pi))
    exponent = -0.5 * (x / bandwidth) ** 2
    return math.exp(exponent) / (bandwidth * math.sqrt(2 * math.pi))


@njit(cache=True, fastmath=True)
def epanechnikov_kernel(x: float, bandwidth: float) -> float:
    """
    Epanechnikov核函数（计算更快）
    K(u) = 0.75 * (1 - u^2) for |u| <= 1, else 0
    """
    u = x / bandwidth
    if abs(u) <= 1:
        return 0.75 * (1 - u * u) / bandwidth
    return 0.0


@njit(cache=True, fastmath=True)
def fast_kde_epanechnikov(
        points: np.ndarray,
        grid: np.ndarray,
        bandwidth: float
) -> np.ndarray:
    """
    使用Epanechnikov核的快速KDE计算

    Args:
        points: 数据点数组 (n_samples,)
        grid: 评估网格数组 (n_grid,)
        bandwidth: 带宽参数

    Returns:
        密度估计数组 (n_grid,)
    """
    n_samples = len(points)
    n_grid = len(grid)

    if n_samples == 0 or n_grid == 0:
        return np.zeros(0)

    # Epanechnikov核的归一化因子
    norm_factor = 1.0 / n_samples

    kde_values = np.zeros(n_grid)

    for i in range(n_grid):
        grid_val = grid[i]
        density_sum = 0.0

        for j in range(n_samples):
            diff = points[j] - grid_val
            density_sum += epanechnikov_kernel(diff, bandwidth)

        kde_values[i] = density_sum * norm_factor

    return kde_values


@njit(cache=True, fastmath=True)
def fast_kde_vectorized(
        points: np.ndarray,
        grid: np.ndarray,
        bandwidth: float
) -> np.ndarray:
    """
    向量化KDE计算（使用numpy广播）

    Args:
        points: 数据点数组 (n_samples,)
        grid: 评估网格数组 (n_grid,)
        bandwidth: 带宽参数

    Returns:
        密度估计数组 (n_grid,)
    """
    n_samples = len(points)
    n_grid = len(grid)

    if n_samples == 0 or n_grid == 0:
        return np.zeros(0)

    # 预计算常数
    norm_factor = 1.0 / (n_samples * bandwidth * math.sqrt(2 * math.pi))
    bandwidth_sq = bandwidth * bandwidth

    # 使用广播计算所有差异
    # 注意：这可能会使用更多内存，但对于小数组可以接受
    diffs = grid.reshape(-1, 1) - points.reshape(1, -1)  # (n_grid, n_samples)

    # 计算高斯核
    exponents = -0.5 * diffs * diffs / bandwidth_sq
    kde_values = np.exp(exponents).sum(axis=1) * norm_factor

    return kde_values


@njit(cache=True, fastmath=True, parallel=True)
def kde_density_1d(
        points: np.ndarray,
        grid: np.ndarray,
        bandwidth: float
) -> np.ndarray:
    """
    一维KDE密度估计（Numba加速，并行计算）

    Args:
        points: 数据点数组 (n_samples,)
        grid: 评估网格点数组 (n_grid,)
        bandwidth: 带宽参数

    Returns:
        密度估计数组 (n_grid,)
    """
    n_samples = len(points)
    n_grid = len(grid)

    if n_samples == 0 or n_grid == 0:
        return np.zeros(0)

    densities = np.zeros(n_grid)
    norm_factor = 1.0 / (n_samples * bandwidth * math.sqrt(2 * math.pi))
    bandwidth_sq = bandwidth * bandwidth

    # 并行计算每个网格点的密度
    for i in prange(n_grid):
        grid_val = grid[i]
        density_sum = 0.0

        for j in range(n_samples):
            diff = points[j] - grid_val
            # 直接计算高斯核，避免调用exp函数开销
            exponent = -0.5 * diff * diff / bandwidth_sq
            density_sum += math.exp(exponent)

        densities[i] = density_sum * norm_factor

    return densities


@njit(cache=True, fastmath=True)
def silverman_bandwidth(data: np.ndarray) -> float:
    """
    Silverman带宽选择法则（稳健版本）

    Args:
        data: 数据数组

    Returns:
        带宽值
    """
    n = len(data)
    if n < 2:
        return 1.0  # 默认值

    # 计算标准差
    mean = 0.0
    for i in range(n):
        mean += data[i]
    mean /= n

    variance = 0.0
    for i in range(n):
        diff = data[i] - mean
        variance += diff * diff
    variance /= (n - 1) if n > 1 else 1.0
    std = math.sqrt(variance)

    # Silverman法则: h = 1.06 * σ * n^{-1/5}
    # 使用稳健版本：IQR替代标准差
    # 计算四分位距（IQR）
    sorted_data = np.sort(data)
    q75_idx = int(0.75 * n)
    q25_idx = int(0.25 * n)

    if q75_idx >= n:
        q75_idx = n - 1
    if q25_idx < 0:
        q25_idx = 0

    q75 = sorted_data[q75_idx]
    q25 = sorted_data[q25_idx]
    iqr = q75 - q25

    # 使用更稳健的估计：min(std, iqr/1.34)
    if iqr > 0:
        robust_std = min(std, iqr / 1.34)
    else:
        robust_std = std

    # 计算带宽
    bandwidth = 1.06 * robust_std * (n ** (-0.2))

    # 确保带宽不为零
    if bandwidth <= 0:
        bandwidth = 0.1

    # 金融价格序列的额外保障：最小带宽 = 价格范围 × 0.02 或 至少10个tick size
    price_range = data.max() - data.min()
    tick_size = 0.01  # ETH永续合约tick size
    min_bandwidth_by_range = price_range * 0.02  # 价格范围的2%
    min_bandwidth_by_tick = tick_size * 10  # 10个tick

    if price_range > 0:
        min_bandwidth = max(min_bandwidth_by_range, min_bandwidth_by_tick, tick_size * 5)
        bandwidth = max(bandwidth, min_bandwidth)

    return bandwidth


@njit(cache=True, fastmath=True)
def compute_density_percentiles(
        densities: np.ndarray,
        percentiles: np.ndarray
) -> np.ndarray:
    """
    计算密度分位数（Numba加速）

    Args:
        densities: 密度数组
        percentiles: 分位数数组 (0-100)

    Returns:
        分位数数组
    """
    n = len(densities)
    if n == 0:
        return np.zeros_like(percentiles)

    # 排序密度值
    sorted_densities = np.sort(densities)

    # 计算分位数
    results = np.zeros(len(percentiles))
    for i in range(len(percentiles)):
        p = percentiles[i]
        idx = p * (n - 1) / 100.0
        idx_floor = int(math.floor(idx))
        idx_ceil = int(math.ceil(idx))

        if idx_floor == idx_ceil:
            results[i] = sorted_densities[idx_floor]
        else:
            weight = idx - idx_floor
            results[i] = (sorted_densities[idx_floor] * (1 - weight) +
                          sorted_densities[idx_ceil] * weight)

    return results


@njit(cache=True, fastmath=True)
def find_local_minima(
        grid: np.ndarray,
        densities: np.ndarray,
        window: int = 3
) -> np.ndarray:
    """
    寻找局部极小值点（谷底）

    Args:
        grid: 网格点数组
        densities: 密度数组
        window: 局部搜索窗口大小

    Returns:
        局部极小值点索引数组
    """
    n = len(grid)
    if n < 3:
        return np.zeros(0, dtype=np.int64)

    minima = []

    for i in range(window, n - window):
        is_minimum = True

        # 检查当前点是否比左右邻居都小
        for j in range(1, window + 1):
            if (densities[i] >= densities[i - j] or
                    densities[i] >= densities[i + j]):
                is_minimum = False
                break

        if is_minimum:
            minima.append(i)

    return np.array(minima, dtype=np.int64)


@njit(cache=True, fastmath=True)
def find_local_maxima(
        grid: np.ndarray,
        densities: np.ndarray,
        window: int = 3
) -> np.ndarray:
    """
    寻找局部极大值点（峰值）

    Args:
        grid: 网格点数组
        densities: 密度数组
        window: 局部搜索局部搜索窗口大小

    Returns:
        局部极大值点索引数组
    """
    n = len(grid)
    if n < 3:
        return np.zeros(0, dtype=np.int64)

    maxima = []

    for i in range(window, n - window):
        is_maximum = True

        # 检查当前点是否比左右邻居都大
        for j in range(1, window + 1):
            if (densities[i] <= densities[i - j] or
                    densities[i] <= densities[i + j]):
                is_maximum = False
                break

        if is_maximum:
            maxima.append(i)

    return np.array(maxima, dtype=np.int64)


@njit(cache=True, fastmath=True)
def compute_density_gradient(
        densities: np.ndarray,
        grid_spacing: float
) -> np.ndarray:
    """
    计算密度梯度（一阶导数）

    Args:
        densities: 密度数组
        grid_spacing: 网格间距

    Returns:
        梯度数组
    """
    n = len(densities)
    if n < 2:
        return np.zeros(n)

    gradient = np.zeros(n)

    # 中心差分（内部点）
    for i in range(1, n - 1):
        gradient[i] = (densities[i + 1] - densities[i - 1]) / (2 * grid_spacing)

    # 边界点：前向/后向差分
    if n > 1:
        gradient[0] = (densities[1] - densities[0]) / grid_spacing
        gradient[-1] = (densities[-1] - densities[-2]) / grid_spacing

    return gradient


@njit(cache=True, fastmath=True)
def compute_density_curvature(
        densities: np.ndarray,
        grid_spacing: float
) -> np.ndarray:
    """
    计算密度曲率（二阶导数）

    Args:
        densities: 密度数组
        grid_spacing: 网格间距

    Returns:
        曲率数组
    """
    n = len(densities)
    if n < 3:
        return np.zeros(n)

    curvature = np.zeros(n)
    spacing_sq = grid_spacing * grid_spacing

    # 中心差分
    for i in range(1, n - 1):
        curvature[i] = (densities[i + 1] - 2 * densities[i] + densities[i - 1]) / spacing_sq

    # 边界点：使用二阶精度公式
    if n > 2:
        curvature[0] = (2 * densities[0] - 5 * densities[1] + 4 * densities[2] - densities[3]) / spacing_sq
        curvature[-1] = (2 * densities[-1] - 5 * densities[-2] + 4 * densities[-3] - densities[-4]) / spacing_sq

    return curvature


class KDECore:
    """
    KDE核心计算器
    封装Numba加速的KDE计算功能
    """

    def __init__(self, config: KDEEngineConfig):
        """
        初始化KDE核心计算器

        Args:
            config: KDE引擎配置
        """
        self.config = config
        self.grid_size = 50  # 固定网格策略的默认大小（如果adaptive_grid=False时使用）
        self.cached_bandwidth: Optional[float] = None

        # 预热Numba JIT编译的函数
        self._warmup_numba_functions()

        logger.info(f"KDECore初始化完成，配置: {config}")
        logger.info(f"  自适应网格: {config.adaptive_grid}, 目标步长: {config.target_grid_step}, "
                    f"网格范围: [{config.min_grid_size}, {config.max_grid_size}]")

    def compute_kde(self, prices: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        计算KDE密度估计

        Args:
            prices: 价格数组

        Returns:
            (网格点, 密度估计)
        """
        if len(prices) < self.config.min_slice_ticks:
            logger.warning(f"数据点不足，跳过KDE计算: {len(prices)} < {self.config.min_slice_ticks}")
            return np.array([]), np.array([])

        # 计算带宽
        bandwidth = self._compute_bandwidth(prices)

        # 创建评估网格
        grid = self._create_grid(prices)

        # 计算密度估计 - 使用Epanechnikov核（更快）
        densities = fast_kde_epanechnikov(prices, grid, bandwidth)

        # 调试日志
        logger.debug(
            f"KDE计算完成: 输入点数={len(prices)}, 带宽={bandwidth:.4f}, "
            f"网格大小={len(grid)}, 价格范围=[{np.min(prices):.2f}, {np.max(prices):.2f}], "
            f"网格范围=[{np.min(grid):.2f}, {np.max(grid):.2f}], "
            f"密度范围=[{np.min(densities):.2e}, {np.max(densities):.2e}]"
        )

        return grid, densities

    def _compute_bandwidth(self, prices: np.ndarray) -> float:
        """
        计算带宽参数

        Args:
            prices: 价格数组

        Returns:
            带宽值
        """
        bandwidth = silverman_bandwidth(prices)
        logger.debug(f"带宽计算: 输入点数={len(prices)}, 价格范围=[{prices.min():.2f}, {prices.max():.2f}], 带宽={bandwidth:.4f}")
        return bandwidth

    def _create_grid(self, prices: np.ndarray) -> np.ndarray:
        """
        创建评估网格（支持自适应和固定两种策略）

        Args:
            prices: 价格数组

        Returns:
            网格点数组
        """
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

        if self.config.adaptive_grid:
            # 自适应网格策略：基于目标步长动态调整网格点数
            if self.config.target_grid_step > 0:
                required_points = int(extended_range / self.config.target_grid_step) + 1
            else:
                required_points = self.config.min_grid_size

            # 限制在[min_grid_size, max_grid_size]范围内
            n_points = max(self.config.min_grid_size, min(required_points, self.config.max_grid_size))

            # 确保不超过样本数量
            n_points = min(n_points, len(prices))
        else:
            # 固定网格策略
            n_points = min(self.grid_size, len(prices))

        # 确保至少3个点
        n_points = max(n_points, 3)

        return np.linspace(grid_min, grid_max, n_points)

    def _warmup_numba_functions(self):
        """
        预热Numba JIT编译的函数，减少首次运行延迟
        """
        try:
            # 创建小型测试数据
            test_prices = np.random.randn(10) * 50 + 3000
            test_grid = np.linspace(2900, 3100, 20)
            test_densities = np.random.random(20)
            test_percentiles = np.array([30.0])

            # 预热核心函数
            bandwidth = silverman_bandwidth(test_prices)
            fast_kde_epanechnikov(test_prices, test_grid, bandwidth)
            fast_kde_vectorized(test_prices, test_grid, bandwidth)
            kde_density_1d(test_prices, test_grid, bandwidth)  # 也预热原始版本
            compute_density_percentiles(test_densities, test_percentiles)
            find_local_minima(test_grid, test_densities)
            find_local_maxima(test_grid, test_densities)

            logger.debug("Numba函数预热完成")
        except Exception as e:
            logger.warning(f"Numba预热异常: {e}")

    def compute_density_percentile_threshold(
            self,
            densities: np.ndarray,
            percentile: float = None
    ) -> float:
        """
        计算密度分位数阈值

        Args:
            densities: 密度数组
            percentile: 分位数（0-100），None使用配置中的值

        Returns:
            阈值
        """
        if len(densities) == 0:
            return 0.0

        if percentile is None:
            percentile = self.config.lvn_density_percentile

        percentiles_array = np.array([percentile])
        threshold = compute_density_percentiles(densities, percentiles_array)[0]

        return threshold

    def find_lvn_candidates(
            self,
            grid: np.ndarray,
            densities: np.ndarray
    ) -> List[Dict[str, float]]:
        """
        查找LVN候选区域

        Args:
            grid: 网格点数组
            densities: 密度数组

        Returns:
            LVN候选区域列表
        """
        if len(grid) == 0 or len(densities) == 0:
            return []

        # 计算密度阈值
        threshold = self.compute_density_percentile_threshold(densities)

        # 寻找局部极小值
        minima_indices = find_local_minima(grid, densities, window=3)

        lvn_candidates = []
        for idx in minima_indices:
            density = densities[idx]

            # 检查是否低于阈值
            if density < threshold:
                lvn_candidates.append({
                    'price': float(grid[idx]),
                    'density': float(density),
                    'threshold': float(threshold),
                    'is_lvn': True
                })

        return lvn_candidates


# 性能测试函数
def test_kde_core_performance():
    """测试KDE核心性能"""
    import time

    logger = get_logger(__name__)

    # 创建测试数据
    n_samples = 10000
    prices = np.random.randn(n_samples) * 50 + 3000

    # 创建配置
    config = KDEEngineConfig()
    kde_core = KDECore(config)

    # 性能测试
    logger.info(f"🔬 KDE核心性能测试 (n={n_samples})")

    # 测试1: KDE计算
    start_time = time.perf_counter()
    grid, densities = kde_core.compute_kde(prices)
    kde_time = time.perf_counter() - start_time

    logger.info(f"  KDE计算时间: {kde_time * 1000:.1f}ms")
    logger.info(f"  网格大小: {len(grid)}")
    logger.info(f"  密度范围: {np.min(densities):.2e} - {np.max(densities):.2e}")

    # 测试2: 局部极小值检测
    if len(densities) > 0:
        start_time = time.perf_counter()
        minima = find_local_minima(grid, densities)
        minima_time = time.perf_counter() - start_time

        logger.info(f"  局部极小值检测: {len(minima)} 个，时间: {minima_time * 1000:.1f}ms")

        # 测试3: LVN候选检测
        start_time = time.perf_counter()
        lvn_candidates = kde_core.find_lvn_candidates(grid, densities)
        lvn_time = time.perf_counter() - start_time

        logger.info(f"  LVN候选检测: {len(lvn_candidates)} 个，时间: {lvn_time * 1000:.1f}ms")

    return kde_time


if __name__ == "__main__":
    # 运行性能测试
    test_kde_core_performance()
