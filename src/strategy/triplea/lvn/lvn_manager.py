#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四号引擎v3.0 LVN区域管理器
负责LVN区域的合并、冲突解决和生命周期管理
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import numpy as np

from src.strategy.triplea.core.data_structures import KDEEngineConfig
from src.strategy.triplea.kde.lvn_extractor import LVNRegion, LVNExtractor
from src.utils.log import get_logger

logger = get_logger(__name__)


@dataclass
class LVNCluster:
    """
    LVN区域簇（合并后的区域）
    """
    cluster_id: int
    regions: List[LVNRegion] = field(default_factory=list)

    # 合并后的属性
    merged_start_price: float = 0.0
    merged_end_price: float = 0.0
    merged_min_density: float = float('inf')
    merged_min_price: float = 0.0

    # 统计信息
    first_detected_time: float = None
    last_updated_time: float = None
    detection_count: int = 0

    # 状态
    is_active: bool = True
    confidence: float = 0.0  # 置信度（0-1）

    def __post_init__(self):
        """后初始化处理"""
        if self.first_detected_time is None:
            self.first_detected_time = time.time()
        if self.last_updated_time is None:
            self.last_updated_time = time.time()

    def update_merged_attributes(self):
        """更新合并后的属性"""
        if not self.regions:
            return

        # 计算合并后的价格范围
        self.merged_start_price = min(r.start_price for r in self.regions)
        self.merged_end_price = max(r.end_price for r in self.regions)

        # 找到最小密度对应的价格
        min_density_region = min(self.regions, key=lambda r: r.min_density)
        self.merged_min_density = min_density_region.min_density
        self.merged_min_price = min_density_region.min_price

        # 更新统计信息
        self.last_updated_time = time.time()
        self.detection_count += 1

        # 计算置信度（基于重复检测次数和区域稳定性）
        self.confidence = min(1.0, self.detection_count / 10.0)

    def contains_region(self, region: LVNRegion, tolerance: float = 0.5) -> bool:
        """
        检查是否包含给定区域（考虑容差）

        Args:
            region: 待检查区域
            tolerance: 价格容差（美元）

        Returns:
            是否包含
        """
        # 区域重叠判断
        return (self.merged_start_price - tolerance <= region.end_price and
                self.merged_end_price + tolerance >= region.start_price)

    def merge_region(self, region: LVNRegion) -> bool:
        """
        合并新的区域到簇中

        Args:
            region: 待合并区域

        Returns:
            是否成功合并
        """
        if not self.contains_region(region):
            return False

        self.regions.append(region)
        self.update_merged_attributes()
        return True

    def get_cluster_metrics(self) -> Dict[str, float]:
        """
        获取簇的度量指标

        Returns:
            度量指标字典
        """
        if not self.regions:
            return {}

        # 计算簇的稳定性指标
        age_hours = (time.time() - self.first_detected_time) / 3600

        # 区域密度一致性
        densities = [r.min_density for r in self.regions]
        density_std = np.std(densities) if len(densities) > 1 else 0

        # 区域大小一致性
        widths = [r.end_price - r.start_price for r in self.regions]
        width_std = np.std(widths) if len(widths) > 1 else 0

        return {
            'cluster_id': self.cluster_id,
            'age_hours': age_hours,
            'region_count': len(self.regions),
            'density_std': density_std,
            'width_std': width_std,
            'cluster_width': self.merged_end_price - self.merged_start_price,
            'confidence': self.confidence,
            'detection_count': self.detection_count
        }

    def __repr__(self) -> str:
        return (f"LVNCluster(id={self.cluster_id}, "
                f"regions={len(self.regions)}, "
                f"range=[{self.merged_start_price:.2f}, {self.merged_end_price:.2f}], "
                f"min_price={self.merged_min_price:.2f}, "
                f"confidence={self.confidence:.2f})")


class LVNManager:
    """
    LVN区域管理器
    负责区域的合并、冲突解决和生命周期管理
    """

    def __init__(self, config: KDEEngineConfig):
        """
        初始化LVN管理器

        Args:
            config: KDE引擎配置
        """
        self.config = config

        # 提取器
        self.extractor = LVNExtractor(config)

        # 簇管理
        self.clusters: Dict[int, LVNCluster] = {}
        self.next_cluster_id = 0

        # 区域缓存（用于历史追踪）
        self.region_history: Dict[int, List[LVNRegion]] = {}
        self.max_history_size = 100

        # 配置参数
        self.merge_tolerance = 0.5  # 合并容差（美元）
        self.max_cluster_age_hours = 24  # 簇最大寿命（小时）
        self.min_cluster_confidence = 0.1  # 最小置信度（实盘稳定需要容忍新簇）
        self.cluster_inactive_threshold = 7200  # 簇不活跃阈值（秒）- 2小时

        # 性能统计
        self.stats = {
            'total_regions_detected': 0,
            'regions_merged': 0,
            'clusters_created': 0,
            'clusters_expired': 0
        }

        logger.info(f"LVNManager初始化完成")

    def process_kde_result(
            self,
            grid: np.ndarray,
            densities: np.ndarray,
            timestamp: float = None
    ) -> List[LVNCluster]:
        """
        处理KDE计算结果，提取并管理LVN区域

        Args:
            grid: KDE网格点数组
            densities: KDE密度数组
            timestamp: 当前时间戳（秒）

        Returns:
            活跃的LVN簇列表
        """
        if timestamp is None:
            timestamp = time.time()

        # 提取LVN区域
        detected_regions = self.extractor.extract_from_kde(grid, densities)
        self.stats['total_regions_detected'] += len(detected_regions)

        if not detected_regions:
            # 清理过期簇
            self._cleanup_inactive_clusters(timestamp)
            return []

        # 合并区域到现有簇
        merged_regions = []
        for region in detected_regions:
            merged = False

            # 尝试合并到现有簇
            for cluster in self.clusters.values():
                if cluster.contains_region(region, self.merge_tolerance):
                    if cluster.merge_region(region):
                        merged = True
                        self.stats['regions_merged'] += 1
                        break

            # 如果无法合并到现有簇，创建新簇
            if not merged:
                cluster = LVNCluster(
                    cluster_id=self.next_cluster_id,
                    regions=[region]
                )
                cluster.update_merged_attributes()
                self.clusters[self.next_cluster_id] = cluster
                self.next_cluster_id += 1
                self.stats['clusters_created'] += 1

            # 保存区域到历史记录
            self._add_region_to_history(region, timestamp)

        # 清理过期簇
        self._cleanup_inactive_clusters(timestamp)

        # 返回活跃簇
        active_clusters = [c for c in self.clusters.values() if c.is_active]

        logger.debug(f"处理KDE结果: 检测到{len(detected_regions)}区域, "
                     f"活跃簇{len(active_clusters)}个")

        return active_clusters

    def get_active_clusters(self) -> List[LVNCluster]:
        """
        获取当前活跃的LVN簇

        Returns:
            活跃簇列表
        """
        return [c for c in self.clusters.values() if c.is_active]

    def get_cluster_by_id(self, cluster_id: int) -> Optional[LVNCluster]:
        """
        根据ID获取LVN簇

        Args:
            cluster_id: 簇ID

        Returns:
            LVN簇对象，如果不存在则返回None
        """
        return self.clusters.get(cluster_id)

    def find_closest_cluster(
            self,
            price: float,
            max_distance: float = 10.0
    ) -> Optional[LVNCluster]:
        """
        查找距离给定价格最近的LVN簇

        Args:
            price: 目标价格
            max_distance: 最大距离限制（美元）

        Returns:
            最近的LVN簇，如果不存在则返回None
        """
        active_clusters = self.get_active_clusters()
        if not active_clusters:
            return None

        closest_cluster = None
        min_distance = float('inf')

        for cluster in active_clusters:
            # 如果价格在簇范围内
            if (cluster.merged_start_price <= price <= cluster.merged_end_price):
                return cluster

            # 计算到簇边界的距离
            if price < cluster.merged_start_price:
                distance = cluster.merged_start_price - price
            else:  # price > cluster.merged_end_price
                distance = price - cluster.merged_end_price

            if distance < min_distance and distance <= max_distance:
                min_distance = distance
                closest_cluster = cluster

        return closest_cluster

    def update_cluster_confidence(
            self,
            cluster_id: int,
            price_action_data: Dict[str, any]
    ) -> float:
        """
        根据价格行为更新簇的置信度

        Args:
            cluster_id: 簇ID
            price_action_data: 价格行为数据

        Returns:
            更新后的置信度
        """
        cluster = self.get_cluster_by_id(cluster_id)
        if not cluster:
            return 0.0

        # 基于价格在簇区域内的停留时间增加置信度
        if 'time_in_region' in price_action_data:
            time_in_region = price_action_data['time_in_region']
            confidence_increase = min(0.1, time_in_region / 300)  # 最多增加0.1，每5分钟增加0.1
            cluster.confidence = min(1.0, cluster.confidence + confidence_increase)

        # 基于价格围绕簇的振荡增加置信度
        if 'oscillations_around_region' in price_action_data:
            oscillations = price_action_data['oscillations_around_region']
            cluster.confidence = min(1.0, cluster.confidence + oscillations * 0.05)

        # 基于簇的年龄衰减置信度（但不会低于基础置信度）
        age_hours = (time.time() - cluster.first_detected_time) / 3600
        if age_hours > 1 and cluster.confidence > 0.5:
            age_decay = min(0.1, (age_hours - 1) * 0.02)
            cluster.confidence = max(self.min_cluster_confidence, cluster.confidence - age_decay)

        return cluster.confidence

    def _cleanup_inactive_clusters(self, current_time: float):
        """
        清理不活跃或过期的簇

        Args:
            current_time: 当前时间戳
        """
        clusters_to_remove = []

        for cluster_id, cluster in self.clusters.items():
            # 检查簇是否过期
            age_seconds = current_time - cluster.first_detected_time
            # 簇过期条件
            cluster_expired = False

            # 1. 年龄超过最大寿命
            if age_seconds > self.max_cluster_age_hours * 3600:
                cluster_expired = True
                logger.debug(f"簇 {cluster_id} 因年龄过期 ({age_seconds / 3600:.1f}小时)")

            # 2. 置信度过低
            elif cluster.confidence < self.min_cluster_confidence:
                cluster_expired = True
                logger.debug(f"簇 {cluster_id} 因置信度过低过期 ({cluster.confidence:.2f})")

            # 3. 长时间不活跃
            elif (current_time - cluster.last_updated_time) > self.cluster_inactive_threshold:
                cluster_expired = True
                logger.debug(f"簇 {cluster_id} 因不活跃过期 ({current_time - cluster.last_updated_time:.0f}秒)")

            # 4. 簇内区域数量过少且年龄较大
            elif len(cluster.regions) < 3 and age_seconds > 3600:
                cluster_expired = True
                logger.debug(f"簇 {cluster_id} 因区域过少过期 ({len(cluster.regions)}个区域)")

            if cluster_expired:
                cluster.is_active = False
                clusters_to_remove.append(cluster_id)

        # 移除过期簇
        for cluster_id in clusters_to_remove:
            del self.clusters[cluster_id]
            self.stats['clusters_expired'] += 1

        if clusters_to_remove:
            logger.info(f"清理了 {len(clusters_to_remove)} 个过期簇")

    def _add_region_to_history(self, region: LVNRegion, timestamp: float):
        """
        添加区域到历史记录

        Args:
            region: LVN区域
            timestamp: 时间戳
        """
        region_id = region.region_id

        if region_id not in self.region_history:
            self.region_history[region_id] = deque(maxlen=self.max_history_size)

        # 保存区域快照
        history_entry = {
            'timestamp': timestamp,
            'start_price': region.start_price,
            'end_price': region.end_price,
            'min_price': region.min_price,
            'min_density': region.min_density,
            'detected_time': time.time()
        }

        self.region_history[region_id].append(history_entry)

    def get_region_history(self, region_id: int) -> List[Dict]:
        """
        获取区域历史记录

        Args:
            region_id: 区域ID

        Returns:
            区域历史记录列表
        """
        return list(self.region_history.get(region_id, []))

    def get_cluster_evolution(self, cluster_id: int) -> List[Dict]:
        """
        获取簇的演化历史

        Args:
            cluster_id: 簇ID

        Returns:
            簇演化历史列表
        """
        cluster = self.get_cluster_by_id(cluster_id)
        if not cluster:
            return []

        evolution = []

        for region in cluster.regions:
            history = self.get_region_history(region.region_id)
            if history:
                evolution.append({
                    'region_id': region.region_id,
                    'history': history
                })

        return evolution

    def get_statistics(self) -> Dict[str, any]:
        """
        获取管理器统计信息

        Returns:
            统计信息字典
        """
        stats = self.stats.copy()

        # 添加当前状态信息
        stats.update({
            'active_clusters_count': len(self.get_active_clusters()),
            'total_clusters_count': len(self.clusters),
            'region_history_count': sum(len(h) for h in self.region_history.values()),
            'unique_regions_tracked': len(self.region_history)
        })

        return stats

    def reset(self):
        """
        重置管理器状态
        """
        self.clusters.clear()
        self.region_history.clear()
        self.next_cluster_id = 0

        # 重置统计
        for key in self.stats:
            self.stats[key] = 0

        logger.info("LVNManager已重置")


def test_lvn_manager():
    """
    测试LVN管理器功能
    """
    logger = get_logger(__name__)

    print("🔬 测试LVN管理器")
    print("=" * 60)

    # 创建配置
    config = KDEEngineConfig()
    manager = LVNManager(config)

    # 创建测试数据（模拟KDE计算结果）
    np.random.seed(42)

    # 场景1：检测到区域
    test_grid = np.linspace(2950, 3050, 100)
    test_densities = np.random.random(100) * 100

    # 处理KDE结果
    clusters = manager.process_kde_result(test_grid, test_densities)

    print(f"检测到 {len(clusters)} 个簇")

    # 场景2：重复检测（区域合并）
    test_densities2 = np.random.random(100) * 100

    # 稍微修改密度以模拟新的检测
    test_densities2[40:60] = test_densities2[40:60] * 0.5  # 在50附近创建低密度区域

    clusters2 = manager.process_kde_result(test_grid, test_densities2)

    print(f"第二次检测后: {len(clusters2)} 个簇")

    # 获取统计信息
    stats = manager.get_statistics()
    print(f"统计信息: 总区域 {stats['total_regions_detected']}, "
          f"合并区域 {stats['regions_merged']}, "
          f"创建簇 {stats['clusters_created']}")

    # 测试簇查询功能

    if clusters:
        cluster = clusters[0]

        # 测试簇度量
        metrics = cluster.get_cluster_metrics()
        print(f"簇度量: ID={metrics['cluster_id']}, "
              f"区域数={metrics['region_count']}, "
              f"宽度={metrics['cluster_width']:.2f}, "
              f"置信度={metrics['confidence']:.2f}")

        # 测试最近簇查找
        test_price = 3000.0
        closest = manager.find_closest_cluster(test_price, max_distance=50.0)
        if closest:
            print(f"价格 {test_price} 最近的簇: ID={closest.cluster_id}, "
                  f"范围=[{closest.merged_start_price:.2f}, {closest.merged_end_price:.2f}]")

    print("✅ LVN管理器功能测试完成")

    return manager


if __name__ == "__main__":
    # 运行测试
    manager = test_lvn_manager()
