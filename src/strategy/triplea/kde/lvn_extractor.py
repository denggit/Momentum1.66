"""
四号引擎v3.0 LVN区域提取器
基于KDE密度估计的局部低成交量节点检测
使用局部极小值搜索和密度阈值过滤
"""

from typing import List, Tuple, Dict, Optional

import numpy as np
from numba import njit, prange

from src.strategy.triplea.core.data_structures import KDEEngineConfig
from src.utils.log import get_logger

logger = get_logger(__name__)


@njit(cache=True, fastmath=True)
def find_valleys(
        grid: np.ndarray,
        densities: np.ndarray,
        min_depth: float = 0.1,
        min_width: float = 0.0
):
    """
    寻找山谷区域（连续低密度区域）

    Args:
        grid: 网格点数组
        densities: 密度数组
        min_depth: 最小深度（相对深度）
        min_width: 最小宽度（网格点数量）

    Returns:
        [(start_idx, end_idx, min_density), ...] 列表
    """
    n = len(grid)
    if n < 3:
        # 返回空数组
        return np.zeros((0, 3), dtype=np.float64)

    # 预分配最大可能数量的山谷（每个点最多一个山谷）
    max_valleys = n // 2
    valley_starts = np.zeros(max_valleys, dtype=np.int64)
    valley_ends = np.zeros(max_valleys, dtype=np.int64)
    valley_min_densities = np.zeros(max_valleys, dtype=np.float64)

    valley_count = 0
    in_valley = False
    valley_start = 0
    valley_min_density = 1e100  # 使用大数字代替inf
    valley_min_idx = 0

    # 计算全局密度范围用于归一化
    max_density = np.max(densities)
    min_density_all = np.min(densities)
    density_range = max_density - min_density_all

    if density_range == 0:
        return np.zeros((0, 3), dtype=np.float64)

    for i in range(1, n - 1):
        current_density = densities[i]

        # 检查是否为局部极小值
        is_minimum = (densities[i] < densities[i - 1] and
                      densities[i] < densities[i + 1])

        # 检查是否低于相邻点
        is_low = (densities[i] < 0.5 * (densities[i - 1] + densities[i + 1]))

        if (is_minimum or is_low) and not in_valley:
            # 开始新的山谷
            in_valley = True
            valley_start = i
            valley_min_density = current_density
            valley_min_idx = i

        elif in_valley:
            # 更新山谷内的最小密度
            if current_density < valley_min_density:
                valley_min_density = current_density
                valley_min_idx = i

            # 检查山谷是否结束（密度显著上升）
            density_increase = (current_density - valley_min_density) / density_range
            if density_increase > min_depth * 2 and i - valley_start >= min_width:
                # 结束当前山谷
                valley_starts[valley_count] = valley_start
                valley_ends[valley_count] = i
                valley_min_densities[valley_count] = valley_min_density
                valley_count += 1
                in_valley = False

    # 处理最后一个山谷
    if in_valley and n - 1 - valley_start >= min_width:
        valley_starts[valley_count] = valley_start
        valley_ends[valley_count] = n - 1
        valley_min_densities[valley_count] = valley_min_density
        valley_count += 1

    # 合并结果到单个数组
    if valley_count > 0:
        valleys = np.zeros((valley_count, 3), dtype=np.float64)
        for i in range(valley_count):
            valleys[i, 0] = valley_starts[i]
            valleys[i, 1] = valley_ends[i]
            valleys[i, 2] = valley_min_densities[i]
        return valleys
    else:
        return np.zeros((0, 3), dtype=np.float64)


@njit(cache=True, fastmath=True)
def compute_valley_metrics(
        grid: np.ndarray,
        densities: np.ndarray,
        valley_start: int,
        valley_end: int
) -> Dict[str, float]:
    """
    计算山谷区域度量指标

    Args:
        grid: 网格点数组
        densities: 密度数组
        valley_start: 山谷起始索引
        valley_end: 山谷结束索引

    Returns:
        度量指标字典
    """
    # 提取山谷区域
    valley_grid = grid[valley_start:valley_end + 1]
    valley_densities = densities[valley_start:valley_end + 1]

    # 计算基本统计
    min_density_idx = np.argmin(valley_densities)
    min_density = valley_densities[min_density_idx]
    min_price = valley_grid[min_density_idx]

    # 计算山谷宽度（价格范围）
    valley_width = valley_grid[-1] - valley_grid[0]

    # 计算深度（相对密度降低）
    # 使用两侧山峰的平均密度作为参考
    left_ref = densities[max(0, valley_start - 1)]
    right_ref = densities[min(len(densities) - 1, valley_end + 1)]
    ref_density = 0.5 * (left_ref + right_ref)

    if ref_density > 0:
        depth_ratio = (ref_density - min_density) / ref_density
    else:
        depth_ratio = 0.0

    # 计算面积（密度积分）
    area = 0.0
    for i in range(len(valley_grid) - 1):
        dx = valley_grid[i + 1] - valley_grid[i]
        avg_density = 0.5 * (valley_densities[i] + valley_densities[i + 1])
        area += dx * avg_density

    return {
        'start_price': float(valley_grid[0]),
        'end_price': float(valley_grid[-1]),
        'min_price': float(min_price),
        'min_density': float(min_density),
        'width': float(valley_width),
        'depth_ratio': float(depth_ratio),
        'area': float(area)
    }


@njit(cache=True, fastmath=True, parallel=True)
def extract_all_valleys_parallel(
        grid: np.ndarray,
        densities: np.ndarray,
        min_depth: float = 0.1,
        min_width: int = 3
) -> List[Dict[str, float]]:
    """
    并行提取所有山谷区域

    Args:
        grid: 网格点数组
        densities: 密度数组
        min_depth: 最小深度阈值
        min_width: 最小宽度（网格点数）

    Returns:
        山谷区域列表
    """
    n = len(grid)
    if n < 3:
        return []

    # 首先寻找所有山谷
    valleys = find_valleys(grid, densities, min_depth, min_width)

    # 并行计算每个山谷的度量指标
    results = []

    for valley_idx in prange(len(valleys)):
        start_idx, end_idx, min_density = valleys[valley_idx]

        # 计算度量指标
        metrics = compute_valley_metrics(grid, densities, start_idx, end_idx)
        metrics['valley_id'] = valley_idx

        # 转换结果（Numba不支持直接返回字典列表，所以这里简化处理）
        # 在实际使用中，需要将结果收集起来

    # 注意：Numba的并行循环不能直接返回复杂结构
    # 这里返回简化版本
    return []


class LVNRegion:
    """
    LVN区域表示类
    """

    def __init__(
            self,
            region_id: int,
            price_range: Tuple[float, float],
            min_price: float,
            min_density: float,
            metrics: Dict[str, float]
    ):
        """
        初始化LVN区域

        Args:
            region_id: 区域ID
            price_range: 价格范围 (start, end)
            min_price: 最低密度对应的价格
            min_density: 最低密度值
            metrics: 度量指标字典
        """
        self.region_id = region_id
        self.start_price = price_range[0]
        self.end_price = price_range[1]
        self.min_price = min_price
        self.min_density = min_density
        self.metrics = metrics

        # 计算中心价格
        self.center_price = (self.start_price + self.end_price) / 2.0

        # 区域状态
        self.is_active = True
        self.detection_time = None
        self.last_update_time = None

    def contains_price(self, price: float) -> bool:
        """
        检查价格是否在LVN区域内

        Args:
            price: 待检查价格

        Returns:
            是否在区域内
        """
        return self.start_price <= price <= self.end_price

    def distance_to_center(self, price: float) -> float:
        """
        计算价格到区域中心的距离

        Args:
            price: 待计算价格

        Returns:
            距离值
        """
        return abs(price - self.center_price)

    def __repr__(self) -> str:
        return (f"LVNRegion(id={self.region_id}, "
                f"price_range=({self.start_price:.2f}, {self.end_price:.2f}), "
                f"center={self.center_price:.2f}, "
                f"width={self.metrics.get('width', 0):.2f})")


class LVNExtractor:
    """
    LVN区域提取器
    从KDE密度估计中提取低成交量节点区域
    """

    def __init__(self, config: KDEEngineConfig):
        """
        初始化LVN提取器

        Args:
            config: KDE引擎配置
        """
        self.config = config

        # 提取参数
        self.min_valley_depth = 0.15  # 最小山谷深度（相对）
        self.min_valley_width = 2.0  # 最小山谷宽度（价格单位）
        self.density_percentile_threshold = config.lvn_density_percentile

        # 状态跟踪
        self.detected_regions: List[LVNRegion] = []
        self.region_counter = 0

        logger.info(f"LVNExtractor初始化完成")

    def extract_from_kde(
            self,
            grid: np.ndarray,
            densities: np.ndarray
    ) -> List[LVNRegion]:
        """
        从KDE结果中提取LVN区域

        Args:
            grid: 网格点数组
            densities: 密度数组

        Returns:
            LVN区域列表
        """
        if len(grid) == 0 or len(densities) == 0:
            return []

        # 计算密度阈值
        from src.strategy.triplea.kde.kde_core import compute_density_percentiles
        percentile_array = np.array([self.density_percentile_threshold])
        density_threshold = compute_density_percentiles(densities, percentile_array)[0]

        logger.debug(f"密度阈值 (P{self.density_percentile_threshold}): {density_threshold:.2e}")

        # 寻找山谷区域
        valleys = find_valleys(
            grid, densities,
            min_depth=self.min_valley_depth,
            min_width=2  # 最小网格点数
        )

        lvn_regions = []

        for valley_idx, (start_idx, end_idx, min_density) in enumerate(valleys):
            # 检查是否低于密度阈值
            if min_density > density_threshold:
                continue

            # 计算山谷度量指标
            # 将索引转换为整数（Numba需要整数索引）
            start_idx_int = int(start_idx)
            end_idx_int = int(end_idx)
            metrics = compute_valley_metrics(grid, densities, start_idx_int, end_idx_int)

            # 检查最小宽度要求
            if metrics['width'] < self.min_valley_width:
                continue

            # 创建LVN区域
            region_id = self.region_counter
            self.region_counter += 1

            price_range = (grid[start_idx_int], grid[end_idx_int])
            region = LVNRegion(
                region_id=region_id,
                price_range=price_range,
                min_price=metrics['min_price'],
                min_density=min_density,
                metrics=metrics
            )

            lvn_regions.append(region)
            logger.debug(f"检测到LVN区域 {region_id}: {region}")

        logger.info(f"提取到 {len(lvn_regions)} 个LVN区域")
        return lvn_regions

    def filter_and_merge_regions(
            self,
            regions: List[LVNRegion],
            price_tolerance: float = 0.5
    ) -> List[LVNRegion]:
        """
        过滤和合并重叠的LVN区域

        Args:
            regions: 原始LVN区域列表
            price_tolerance: 价格合并容差

        Returns:
            过滤合并后的区域列表
        """
        if not regions:
            return []

        # 按起始价格排序
        sorted_regions = sorted(regions, key=lambda r: r.start_price)

        merged_regions = []
        current_region = sorted_regions[0]

        for region in sorted_regions[1:]:
            # 检查是否重叠
            if region.start_price <= current_region.end_price + price_tolerance:
                # 合并区域
                current_region.end_price = max(current_region.end_price, region.end_price)
                current_region.center_price = (current_region.start_price + current_region.end_price) / 2.0
                current_region.min_density = min(current_region.min_density, region.min_density)

                # 更新度量指标
                current_region.metrics['width'] = current_region.end_price - current_region.start_price
            else:
                # 开始新区域
                merged_regions.append(current_region)
                current_region = region

        # 添加最后一个区域
        merged_regions.append(current_region)

        logger.debug(f"合并后区域数: {len(merged_regions)} (原始: {len(regions)})")
        return merged_regions

    def find_closest_lvn(
            self,
            price: float,
            regions: List[LVNRegion],
            max_distance: float = 10.0
    ) -> Optional[LVNRegion]:
        """
        查找距离给定价格最近的LVN区域

        Args:
            price: 目标价格
            regions: LVN区域列表
            max_distance: 最大距离限制

        Returns:
            最近的LVN区域，如果没有则返回None
        """
        if not regions:
            return None

        closest_region = None
        min_distance = float('inf')

        for region in regions:
            # 如果价格在区域内
            if region.contains_price(price):
                return region

            # 计算到区域边界的距离
            if price < region.start_price:
                distance = region.start_price - price
            elif price > region.end_price:
                distance = price - region.end_price
            else:
                distance = 0

            if distance < min_distance and distance <= max_distance:
                min_distance = distance
                closest_region = region

        return closest_region

    def update_regions_with_price_action(
            self,
            regions: List[LVNRegion],
            current_price: float,
            price_history: List[float],
            window_size: int = 20
    ) -> List[LVNRegion]:
        """
        根据价格行为更新LVN区域状态

        Args:
            regions: LVN区域列表
            current_price: 当前价格
            price_history: 价格历史
            window_size: 分析窗口大小

        Returns:
            更新后的区域列表
        """
        if not regions or not price_history:
            return regions

        # 分析最近的价格行为
        recent_prices = price_history[-window_size:] if len(price_history) >= window_size else price_history

        if len(recent_prices) < 2:
            return regions

        # 计算价格波动性
        price_array = np.array(recent_prices)
        price_volatility = np.std(price_array)

        # 更新每个区域的状态
        for region in regions:
            # 检查价格是否在区域内或附近
            distance_to_center = region.distance_to_center(current_price)

            # 如果价格在区域内，标记为活跃
            if region.contains_price(current_price):
                region.is_active = True
            # 如果价格远离区域，且波动性高，可能区域已失效
            elif distance_to_center > price_volatility * 3:
                region.is_active = False

        # 过滤掉不活跃的区域
        active_regions = [r for r in regions if r.is_active]

        return active_regions


# 测试函数
def test_lvn_extraction():
    """测试LVN提取功能"""
    import time

    logger = get_logger(__name__)

    # 创建测试数据
    np.random.seed(42)
    n_samples = 10000

    # 模拟包含LVN的价格分布（双峰分布）
    prices1 = np.random.randn(n_samples // 2) * 20 + 2950  # 第一个峰值
    prices2 = np.random.randn(n_samples // 2) * 20 + 3050  # 第二个峰值
    # 中间区域（LVN）样本较少
    prices_mid = np.random.randn(n_samples // 10) * 5 + 3000

    all_prices = np.concatenate([prices1, prices2, prices_mid])

    # 计算KDE
    from src.strategy.triplea.kde.kde_core import KDECore
    from src.strategy.triplea.core.data_structures import KDEEngineConfig

    config = KDEEngineConfig()
    kde_core = KDECore(config)

    logger.info("🔬 LVN提取测试开始")
    start_time = time.perf_counter()

    # 计算KDE
    grid, densities = kde_core.compute_kde(all_prices)
    kde_time = time.perf_counter() - start_time

    logger.info(f"  KDE计算时间: {kde_time * 1000:.1f}ms")
    logger.info(f"  网格大小: {len(grid)}")

    # 提取LVN区域
    extractor = LVNExtractor(config)

    start_time = time.perf_counter()
    lvn_regions = extractor.extract_from_kde(grid, densities)
    extraction_time = time.perf_counter() - start_time

    logger.info(f"  LVN提取时间: {extraction_time * 1000:.1f}ms")
    logger.info(f"  检测到LVN区域数: {len(lvn_regions)}")

    # 输出LVN区域详情
    for i, region in enumerate(lvn_regions):
        logger.info(f"  LVN {i + 1}:")
        logger.info(f"    价格范围: {region.start_price:.2f} - {region.end_price:.2f}")
        logger.info(f"    中心价格: {region.center_price:.2f}")
        logger.info(f"    宽度: {region.metrics['width']:.2f}")
        logger.info(f"    深度比: {region.metrics['depth_ratio']:.3f}")

    # 验证LVN位置（应该在3000附近）
    if len(lvn_regions) > 0:
        for region in lvn_regions:
            if 2980 < region.center_price < 3020:
                logger.info("✅ 成功检测到预期的LVN区域（3000附近）")
                break
        else:
            logger.warning("⚠️ 未检测到3000附近的LVN区域")

    return lvn_regions


if __name__ == "__main__":
    # 运行测试
    test_lvn_extraction()
