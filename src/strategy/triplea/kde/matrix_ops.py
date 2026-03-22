"""
四号引擎v3.0 矩阵操作工具库
提供高性能矩阵广播、向量化计算和数值优化工具
"""

from typing import Tuple, Optional, Union, List, Callable, Dict

import numba
import numpy as np
from numba import njit

from src.utils.log import get_logger

# 类型别名
ArrayLike = Union[np.ndarray, List[float], List[int]]
Shape = Tuple[int, ...]


def broadcast_to_match(
        a: np.ndarray,
        b: np.ndarray,
        axis: Optional[int] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    广播两个数组以匹配形状

    Args:
        a: 第一个数组
        b: 第二个数组
        axis: 广播轴（None表示自动匹配）

    Returns:
        广播后的两个数组
    """
    # 确保输入是numpy数组
    a_arr = np.asarray(a)
    b_arr = np.asarray(b)

    # 如果形状相同，直接返回
    if a_arr.shape == b_arr.shape:
        return a_arr, b_arr

    # 使用numpy的broadcast_arrays进行通用广播
    try:
        # np.broadcast_arrays自动处理NumPy广播规则
        broadcasted = np.broadcast_arrays(a_arr, b_arr)
        return broadcasted[0], broadcasted[1]
    except ValueError:
        # 如果通用广播失败，尝试轴特定广播（如果提供了轴）
        if axis is not None:
            a_shape = list(a_arr.shape)
            b_shape = list(b_arr.shape)

            # 确保轴有效
            if axis < 0:
                axis = len(a_shape) + axis

            # 调整维度数量以匹配
            if len(a_shape) < len(b_shape):
                # 为a添加前导维度
                for _ in range(len(b_shape) - len(a_shape)):
                    a_shape.insert(0, 1)
            elif len(b_shape) < len(a_shape):
                # 为b添加前导维度
                for _ in range(len(a_shape) - len(b_shape)):
                    b_shape.insert(0, 1)

            # 在指定轴上广播
            if a_shape[axis] == 1 and b_shape[axis] != 1:
                a_shape[axis] = b_shape[axis]
                a_broadcast = np.broadcast_to(a_arr, tuple(a_shape))
                return a_broadcast, b_arr
            elif b_shape[axis] == 1 and a_shape[axis] != 1:
                b_shape[axis] = a_shape[axis]
                b_broadcast = np.broadcast_to(b_arr, tuple(b_shape))
                return a_arr, b_broadcast
            else:
                raise ValueError(f"无法在轴 {axis} 上广播数组: {a_arr.shape} vs {b_arr.shape}")
        else:
            raise ValueError(f"无法广播数组: {a_arr.shape} vs {b_arr.shape}")


def sliding_window_view(
        x: np.ndarray,
        window_size: int,
        step: int = 1,
        axis: int = -1
) -> np.ndarray:
    """
    创建滑动窗口视图（无数据复制）

    Args:
        x: 输入数组
        window_size: 窗口大小
        step: 步长
        axis: 滑动轴

    Returns:
        滑动窗口视图 (..., n_windows, window_size, ...)
    """
    # 确保轴为正
    if axis < 0:
        axis = x.ndim + axis

    # 计算新形状
    new_shape = list(x.shape)
    n = new_shape[axis]
    new_shape[axis] = (n - window_size) // step + 1
    new_shape.insert(axis + 1, window_size)

    # 创建步长
    new_strides = list(x.strides)
    new_strides[axis] *= step
    new_strides.insert(axis + 1, x.strides[axis])

    return np.lib.stride_tricks.as_strided(
        x,
        shape=tuple(new_shape),
        strides=tuple(new_strides),
        writeable=False
    )


@njit(cache=True, parallel=False)
def nanmean_axis0(x: np.ndarray) -> np.ndarray:
    """
    沿轴0计算均值（忽略NaN），Numba加速

    Args:
        x: 输入数组 (n_samples, n_features)

    Returns:
        均值数组 (n_features,)
    """
    n_samples, n_features = x.shape
    result = np.zeros(n_features)

    for j in numba.prange(n_features):
        sum_val = 0.0
        count = 0

        for i in range(n_samples):
            val = x[i, j]
            if not np.isnan(val):
                sum_val += val
                count += 1

        if count > 0:
            result[j] = sum_val / count
        else:
            result[j] = np.nan

    return result


@njit(cache=True, parallel=False)
def nanstd_axis0(x: np.ndarray) -> np.ndarray:
    """
    沿轴0计算标准差（忽略NaN），Numba加速

    Args:
        x: 输入数组 (n_samples, n_features)

    Returns:
        标准差数组 (n_features,)
    """
    n_samples, n_features = x.shape
    result = np.zeros(n_features)

    for j in numba.prange(n_features):
        # 计算均值
        sum_val = 0.0
        count = 0

        for i in range(n_samples):
            val = x[i, j]
            if not np.isnan(val):
                sum_val += val
                count += 1

        if count <= 1:
            result[j] = np.nan
            continue

        mean_val = sum_val / count

        # 计算方差
        sum_sq = 0.0
        for i in range(n_samples):
            val = x[i, j]
            if not np.isnan(val):
                diff = val - mean_val
                sum_sq += diff * diff

        result[j] = np.sqrt(sum_sq / (count - 1))

    return result


@njit(cache=True, parallel=True)
def rolling_mean(
        x: np.ndarray,
        window: int,
        min_periods: int = 1
) -> np.ndarray:
    """
    滚动均值计算，Numba并行加速

    Args:
        x: 输入数组 (n_samples,)
        window: 窗口大小
        min_periods: 最小计算周期

    Returns:
        滚动均值数组
    """
    n = len(x)
    result = np.full(n, np.nan)

    # 并行计算每个位置
    for i in numba.prange(window - 1, n):
        start = i - window + 1
        end = i + 1

        # 计算窗口内有效值
        sum_val = 0.0
        count = 0

        for j in range(start, end):
            val = x[j]
            if not np.isnan(val):
                sum_val += val
                count += 1

        if count >= min_periods:
            result[i] = sum_val / count

    return result


@njit(cache=True, parallel=True)
def rolling_std(
        x: np.ndarray,
        window: int,
        min_periods: int = 2
) -> np.ndarray:
    """
    滚动标准差计算，Numba并行加速

    Args:
        x: 输入数组 (n_samples,)
        window: 窗口大小
        min_periods: 最小计算周期

    Returns:
        滚动标准差数组
    """
    n = len(x)
    result = np.full(n, np.nan)

    for i in numba.prange(window - 1, n):
        start = i - window + 1
        end = i + 1

        # 计算均值和方差
        sum_val = 0.0
        sum_sq = 0.0
        count = 0

        for j in range(start, end):
            val = x[j]
            if not np.isnan(val):
                sum_val += val
                sum_sq += val * val
                count += 1

        if count >= min_periods:
            mean_val = sum_val / count
            if count > 1:
                variance = (sum_sq / count) - (mean_val * mean_val)
                # 处理数值误差
                if variance < 0:
                    variance = 0
                result[i] = np.sqrt(variance * count / (count - 1))

    return result


def matrix_broadcast_kde(
        prices: np.ndarray,
        grid_points: np.ndarray,
        bandwidth: float = 0.5
) -> np.ndarray:
    """
    使用矩阵广播计算KDE（核密度估计）

    Args:
        prices: 价格数组 (n_samples,)
        grid_points: 评估网格点 (n_grid,)
        bandwidth: 带宽参数

    Returns:
        KDE值数组 (n_grid,)
    """
    # 使用广播计算差异矩阵
    diff = prices[:, np.newaxis] - grid_points  # 形状 (n_samples, n_grid)

    # 计算高斯核
    kernel = np.exp(-0.5 * (diff / bandwidth) ** 2)

    # 沿样本轴求和并归一化
    kde_values = np.sum(kernel, axis=0) / (len(prices) * bandwidth * np.sqrt(2 * np.pi))

    return kde_values


@njit(cache=True, parallel=True)
def numba_broadcast_kde(
        prices: np.ndarray,
        grid_points: np.ndarray,
        bandwidth: float = 0.5
) -> np.ndarray:
    """
    Numba加速的矩阵广播KDE计算

    Args:
        prices: 价格数组 (n_samples,)
        grid_points: 评估网格点 (n_grid,)
        bandwidth: 带宽参数

    Returns:
        KDE值数组 (n_grid,)
    """
    n_samples = len(prices)
    n_grid = len(grid_points)
    result = np.zeros(n_grid)

    # 预计算常数
    norm_factor = 1.0 / (n_samples * bandwidth * np.sqrt(2 * np.pi))
    bandwidth_sq = bandwidth * bandwidth

    # 并行计算每个网格点
    for j in numba.prange(n_grid):
        grid_val = grid_points[j]
        sum_val = 0.0

        for i in range(n_samples):
            diff = prices[i] - grid_val
            sum_val += np.exp(-0.5 * diff * diff / bandwidth_sq)

        result[j] = sum_val * norm_factor

    return result


def compute_cvd_matrix(
        trades: np.ndarray,
        window_sizes: List[int] = None
) -> Dict[int, np.ndarray]:
    """
    计算多时间窗口的CVD（累积成交量差值）矩阵

    Args:
        trades: 交易数组，包含价格和成交量
        window_sizes: 窗口大小列表

    Returns:
        字典 {窗口大小: CVD数组}
    """
    if window_sizes is None:
        window_sizes = [10, 30, 60, 120]  # 默认窗口大小

    # 提取价格和成交量
    prices = trades['price'].astype(np.float64)
    volumes = trades['volume'].astype(np.float64)
    sides = trades['side']  # 假设有'side'字段：'buy'或'sell'

    # 创建买卖标志
    buy_mask = sides == 'buy'
    sell_mask = sides == 'sell'

    # 计算结果字典
    results = {}

    for window in window_sizes:
        if window > len(trades):
            continue

        # 创建滑动窗口视图
        prices_window = sliding_window_view(prices, window)
        volumes_window = sliding_window_view(volumes, window)
        buy_mask_window = sliding_window_view(buy_mask, window)
        sell_mask_window = sliding_window_view(sell_mask, window)

        # 计算买卖成交量
        buy_volumes = np.sum(volumes_window * buy_mask_window, axis=1)
        sell_volumes = np.sum(volumes_window * sell_mask_window, axis=1)

        # 计算CVD
        cvd = buy_volumes - sell_volumes
        results[window] = cvd

    return results


@njit(cache=True)
def compute_returns(prices: np.ndarray, periods: int = 1) -> np.ndarray:
    """
    计算收益率

    Args:
        prices: 价格数组
        periods: 间隔期数

    Returns:
        收益率数组
    """
    n = len(prices)
    if n <= periods:
        return np.empty(0)

    returns = np.zeros(n - periods)
    for i in range(periods, n):
        returns[i - periods] = (prices[i] - prices[i - periods]) / prices[i - periods]

    return returns


@njit(cache=True, parallel=True)
def compute_correlation_matrix(data: np.ndarray) -> np.ndarray:
    """
    计算相关矩阵，Numba加速

    Args:
        data: 数据矩阵 (n_samples, n_features)

    Returns:
        相关矩阵 (n_features, n_features)
    """
    n_samples, n_features = data.shape
    correlation = np.zeros((n_features, n_features))

    # 计算每对特征的相关性
    for i in numba.prange(n_features):
        for j in range(i, n_features):
            # 提取两个特征
            x = data[:, i]
            y = data[:, j]

            # 计算均值
            mean_x = 0.0
            mean_y = 0.0
            count = 0

            for k in range(n_samples):
                val_x = x[k]
                val_y = y[k]

                if not (np.isnan(val_x) or np.isnan(val_y)):
                    mean_x += val_x
                    mean_y += val_y
                    count += 1

            if count <= 1:
                correlation[i, j] = np.nan
                correlation[j, i] = np.nan
                continue

            mean_x /= count
            mean_y /= count

            # 计算协方差和标准差
            cov = 0.0
            std_x = 0.0
            std_y = 0.0

            for k in range(n_samples):
                val_x = x[k]
                val_y = y[k]

                if not (np.isnan(val_x) or np.isnan(val_y)):
                    diff_x = val_x - mean_x
                    diff_y = val_y - mean_y

                    cov += diff_x * diff_y
                    std_x += diff_x * diff_x
                    std_y += diff_y * diff_y

            if std_x > 0 and std_y > 0:
                corr = cov / np.sqrt(std_x * std_y)
                correlation[i, j] = corr
                correlation[j, i] = corr
            else:
                correlation[i, j] = np.nan
                correlation[j, i] = np.nan

    return correlation


def normalize_matrix(
        matrix: np.ndarray,
        method: str = 'zscore',
        axis: int = 0
) -> np.ndarray:
    """
    矩阵归一化

    Args:
        matrix: 输入矩阵
        method: 归一化方法 ('zscore', 'minmax', 'robust')
        axis: 计算轴

    Returns:
        归一化矩阵
    """
    if method == 'zscore':
        mean = np.nanmean(matrix, axis=axis, keepdims=True)
        std = np.nanstd(matrix, axis=axis, keepdims=True, ddof=1)
        # 避免除零
        std[std == 0] = 1.0
        return (matrix - mean) / std

    elif method == 'minmax':
        min_val = np.nanmin(matrix, axis=axis, keepdims=True)
        max_val = np.nanmax(matrix, axis=axis, keepdims=True)
        # 避免除零
        range_val = max_val - min_val
        range_val[range_val == 0] = 1.0
        return (matrix - min_val) / range_val

    elif method == 'robust':
        median = np.nanmedian(matrix, axis=axis, keepdims=True)
        iqr = np.nanpercentile(matrix, 75, axis=axis, keepdims=True) - \
              np.nanpercentile(matrix, 25, axis=axis, keepdims=True)
        # 避免除零
        iqr[iqr == 0] = 1.0
        return (matrix - median) / iqr

    else:
        raise ValueError(f"未知归一化方法: {method}")


@njit(cache=True)
def fast_quantile(
        data: np.ndarray,
        q: float,
        axis: int = 0
) -> Union[float, np.ndarray]:
    """
    快速分位数计算，Numba加速

    Args:
        data: 输入数组
        q: 分位数 (0-1)
        axis: 计算轴

    Returns:
        分位数（标量或数组）
    """
    if axis == 0 and data.ndim == 1:
        # 一维数组
        sorted_data = np.sort(data[~np.isnan(data)])
        n = len(sorted_data)
        idx = q * (n - 1)
        idx_floor = int(np.floor(idx))
        idx_ceil = int(np.ceil(idx))

        if idx_floor == idx_ceil:
            return sorted_data[idx_floor]
        else:
            weight = idx - idx_floor
            return sorted_data[idx_floor] * (1 - weight) + sorted_data[idx_ceil] * weight

    else:
        # 多维数组（简化实现）
        # 注意：对于高维数组，更复杂的实现需要
        raise NotImplementedError("多维数组分位数计算尚未实现")


# 性能监控装饰器
def measure_performance(func: Callable) -> Callable:
    """
    性能测量装饰器

    Args:
        func: 要测量的函数

    Returns:
        包装后的函数
    """
    import time
    from functools import wraps
    logger = get_logger(__name__)

    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start_time

        # 记录性能指标（存储在包装器上）
        if hasattr(wrapper, '_performance_stats'):
            wrapper._performance_stats.append(elapsed)
        else:
            wrapper._performance_stats = [elapsed]

        # 记录性能信息到日志
        logger.debug(f"{func.__name__}: {elapsed * 1000:.1f}ms")

        return result

    return wrapper


def get_performance_stats(func: Callable) -> Dict[str, float]:
    """
    获取函数性能统计信息

    Args:
        func: 目标函数

    Returns:
        性能统计字典
    """
    if not hasattr(func, '_performance_stats'):
        return {
            'call_count': 0,
            'total_time': 0.0,
            'avg_time': 0.0,
            'min_time': 0.0,
            'max_time': 0.0
        }

    stats = func._performance_stats
    total_time = sum(stats)
    call_count = len(stats)

    return {
        'call_count': call_count,
        'total_time': total_time,
        'avg_time': total_time / call_count if call_count > 0 else 0.0,
        'min_time': min(stats) if stats else 0.0,
        'max_time': max(stats) if stats else 0.0
    }


# 示例使用
if __name__ == "__main__":
    # 设置日志记录器
    logger = get_logger(__name__)

    # 示例1：矩阵广播KDE
    logger.info("示例1: 矩阵广播KDE")
    prices = np.random.randn(1000) * 50 + 3000
    grid = np.linspace(2800, 3200, 200)

    # 纯Python版本
    import time

    start = time.perf_counter()
    kde_python = matrix_broadcast_kde(prices, grid, 0.5)
    python_time = time.perf_counter() - start

    # Numba版本
    start = time.perf_counter()
    kde_numba = numba_broadcast_kde(prices, grid, 0.5)
    numba_time = time.perf_counter() - start

    logger.info(f"Python: {python_time * 1000:.1f}ms")
    logger.info(f"Numba: {numba_time * 1000:.1f}ms")
    logger.info(f"加速比: {python_time / numba_time:.1f}x")

    # 示例2：滑动窗口计算
    logger.info("示例2: 滑动窗口计算")
    data = np.random.randn(10000)
    window = 100

    start = time.perf_counter()
    means = rolling_mean(data, window)
    rolling_time = time.perf_counter() - start

    logger.info(f"滚动均值计算: {rolling_time * 1000:.1f}ms")
    logger.info(f"结果形状: {means.shape}")
    logger.info(f"均值: {np.nanmean(means):.4f}")

    # 示例3：分位数计算
    logger.info("示例3: 分位数计算")
    test_data = np.random.randn(10000)
    quantiles = [0.25, 0.5, 0.75, 0.9, 0.95, 0.99]

    for q in quantiles:
        result = fast_quantile(test_data, q)
        logger.info(f"Q{q * 100:.0f}: {result:.4f}")
