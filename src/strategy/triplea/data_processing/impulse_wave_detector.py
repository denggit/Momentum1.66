#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四号引擎v3.0 脉冲波检测器
基于真实交易数据的专业脉冲波检测算法
结合价格变化率、成交量异常、波动率突破等多维度指标
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Deque
from enum import Enum

import numpy as np

from src.strategy.triplea.core.data_structures import (
    NormalizedTick, KDEEngineConfig
)
from src.utils.log import get_logger

logger = get_logger(__name__)


class ImpulseWaveDirection(Enum):
    """脉冲波方向"""
    BULLISH = "BULLISH"  # 上涨脉冲
    BEARISH = "BEARISH"  # 下跌脉冲
    NONE = "NONE"  # 无明确方向


@dataclass
class ImpulseWave:
    """脉冲波检测结果"""
    start_time: float  # 开始时间（秒）
    end_time: Optional[float] = None  # 结束时间（秒）
    direction: ImpulseWaveDirection = ImpulseWaveDirection.NONE
    start_price: float = 0.0
    end_price: Optional[float] = None
    max_price: float = 0.0
    min_price: float = 0.0
    total_volume: float = 0.0
    net_volume: float = 0.0  # 净成交量（买入-卖出）
    price_change_pct: float = 0.0  # 价格变化百分比
    volatility_score: float = 0.0  # 波动率评分（0-1）
    volume_score: float = 0.0  # 成交量评分（0-1）
    confidence: float = 0.0  # 置信度（0-1）
    is_active: bool = True  # 是否活跃

    def __repr__(self) -> str:
        direction_str = self.direction.value
        duration = "活跃" if self.is_active else f"{self.end_time - self.start_time:.1f}s"
        return (f"ImpulseWave({direction_str}, "
                f"价格变化:{self.price_change_pct:.2f}%, "
                f"置信度:{self.confidence:.2f}, "
                f"持续时间:{duration})")


class ImpulseWaveDetector:
    """
    脉冲波检测器
    基于多维度指标的专业脉冲波检测算法
    """

    def __init__(self, config: KDEEngineConfig):
        """
        初始化脉冲波检测器

        Args:
            config: KDE引擎配置
        """
        self.config = config

        # 数据缓冲区
        self.tick_buffer: Deque[NormalizedTick] = deque(maxlen=200)
        self.price_buffer: Deque[float] = deque(maxlen=200)
        self.volume_buffer: Deque[float] = deque(maxlen=200)
        self.timestamp_buffer: Deque[int] = deque(maxlen=200)  # 纳秒时间戳

        # 统计信息
        self.avg_volume: float = 0.0
        self.volume_std: float = 0.0
        self.historical_volatility: float = 0.0  # 历史波动率

        # 当前脉冲波状态
        self.current_wave: Optional[ImpulseWave] = None
        self.wave_history: List[ImpulseWave] = []

        # 状态跟踪
        self.stats = {
            'total_ticks_processed': 0,
            'waves_detected': 0,
            'false_positives': 0,
            'avg_wave_duration_seconds': 0.0,
            'avg_price_change_pct': 0.0,
            'avg_confidence': 0.0
        }

        logger.info(f"ImpulseWaveDetector初始化完成，配置: {config}")

    def process_tick(self, tick: NormalizedTick) -> Optional[ImpulseWave]:
        """
        处理单个Tick，检测脉冲波

        Args:
            tick: 标准化Tick

        Returns:
            如果检测到脉冲波结束，返回脉冲波对象；否则返回None
        """
        self.tick_buffer.append(tick)
        self.price_buffer.append(tick.px)
        self.volume_buffer.append(tick.sz)
        self.timestamp_buffer.append(tick.ts)
        self.stats['total_ticks_processed'] += 1

        # 更新统计信息
        self._update_statistics()

        # 检查是否有足够数据
        if len(self.tick_buffer) < self.config.lookback_window_ticks:
            return None

        # 检测脉冲波
        wave_result = self._detect_impulse_wave(tick)

        # 处理当前脉冲波
        completed_wave = None
        if self.current_wave:
            completed_wave = self._update_current_wave(tick, wave_result)

        # 如果需要开始新的脉冲波
        if wave_result['is_impulse'] and not self.current_wave:
            self._start_new_wave(tick, wave_result)

        return completed_wave

    def _update_statistics(self) -> None:
        """更新统计信息（成交量、波动率等）"""
        if len(self.volume_buffer) >= 50:
            volumes = np.array(list(self.volume_buffer)[-50:])
            self.avg_volume = np.mean(volumes)
            self.volume_std = np.std(volumes)

        if len(self.price_buffer) >= 100:
            prices = np.array(list(self.price_buffer)[-100:])
            returns = np.diff(prices) / prices[:-1]
            if len(returns) > 0:
                self.historical_volatility = np.std(returns)

    def _detect_impulse_wave(self, current_tick: NormalizedTick) -> Dict:
        """
        检测当前时刻是否为脉冲波

        Args:
            current_tick: 当前Tick

        Returns:
            检测结果字典
        """
        lookback = self.config.lookback_window_ticks
        if len(self.tick_buffer) < lookback:
            return {
                'is_impulse': False,
                'direction': ImpulseWaveDirection.NONE,
                'price_change_pct': 0.0,
                'volume_multiplier': 0.0,
                'volatility_ratio': 0.0,
                'confidence': 0.0
            }

        # 提取回看窗口内的数据
        recent_ticks = list(self.tick_buffer)[-lookback:]
        recent_prices = np.array([t.px for t in recent_ticks])
        recent_volumes = np.array([t.sz for t in recent_ticks])
        recent_timestamps = np.array([t.ts for t in recent_ticks])

        # 计算时间窗口（纳秒转换为秒）
        time_window_ns = recent_timestamps[-1] - recent_timestamps[0]
        time_window_seconds = time_window_ns / 1e9

        # 1. 计算价格变化率
        start_price = recent_prices[0]
        end_price = recent_prices[-1]
        price_change_pct = abs((end_price - start_price) / start_price * 100)

        # 2. 计算成交量异常
        recent_avg_volume = np.mean(recent_volumes)
        volume_multiplier = recent_avg_volume / self.avg_volume if self.avg_volume > 0 else 0

        # 3. 计算波动率突破
        recent_returns = np.diff(recent_prices) / recent_prices[:-1]
        recent_volatility = np.std(recent_returns) if len(recent_returns) > 0 else 0
        volatility_ratio = (recent_volatility / self.historical_volatility
                            if self.historical_volatility > 0 else 0)

        # 4. 计算价格变化方向一致性
        price_direction = 1 if end_price > start_price else -1
        directional_consistency = self._calculate_directional_consistency(recent_prices)

        # 5. 多维度评分
        confidence = self._calculate_confidence(
            price_change_pct=price_change_pct,
            volume_multiplier=volume_multiplier,
            volatility_ratio=volatility_ratio,
            directional_consistency=directional_consistency,
            time_window_seconds=time_window_seconds
        )

        # 6. 判断是否为脉冲波
        is_impulse = (
            price_change_pct >= self.config.min_price_change_pct and
            volume_multiplier >= self.config.min_volume_multiplier and
            volatility_ratio >= self.config.volatility_multiplier and
            confidence >= 0.6  # 最小置信度阈值
        )

        # 确定方向
        direction = ImpulseWaveDirection.NONE
        if is_impulse:
            direction = (ImpulseWaveDirection.BULLISH if price_direction > 0
                         else ImpulseWaveDirection.BEARISH)

        return {
            'is_impulse': is_impulse,
            'direction': direction,
            'price_change_pct': price_change_pct,
            'volume_multiplier': volume_multiplier,
            'volatility_ratio': volatility_ratio,
            'directional_consistency': directional_consistency,
            'confidence': confidence,
            'time_window_seconds': time_window_seconds
        }

    def _calculate_directional_consistency(self, prices: np.ndarray) -> float:
        """
        计算价格变化方向一致性

        Args:
            prices: 价格数组

        Returns:
            一致性评分（0-1）
        """
        if len(prices) < 2:
            return 0.0

        diffs = np.diff(prices)
        positive_diffs = np.sum(diffs > 0)
        negative_diffs = np.sum(diffs < 0)

        total_diffs = len(diffs)
        if total_diffs == 0:
            return 0.0

        # 计算主要方向的比例
        max_same_direction = max(positive_diffs, negative_diffs)
        consistency = max_same_direction / total_diffs

        return consistency

    def _calculate_confidence(
            self,
            price_change_pct: float,
            volume_multiplier: float,
            volatility_ratio: float,
            directional_consistency: float,
            time_window_seconds: float
    ) -> float:
        """
        计算脉冲波置信度

        Args:
            price_change_pct: 价格变化百分比
            volume_multiplier: 成交量倍数
            volatility_ratio: 波动率比率
            directional_consistency: 方向一致性
            time_window_seconds: 时间窗口（秒）

        Returns:
            置信度（0-1）
        """
        # 1. 价格变化评分（0-1）
        price_score = min(1.0, price_change_pct / (self.config.min_price_change_pct * 2))

        # 2. 成交量评分（0-1）
        volume_score = min(1.0, volume_multiplier / (self.config.min_volume_multiplier * 2))

        # 3. 波动率评分（0-1）
        volatility_score = min(1.0, volatility_ratio / (self.config.volatility_multiplier * 2))

        # 4. 时间窗口合理性评分
        time_score = 1.0
        if time_window_seconds < self.config.min_impulse_duration_seconds:
            time_score = time_window_seconds / self.config.min_impulse_duration_seconds
        elif time_window_seconds > self.config.max_impulse_duration_seconds:
            time_score = max(0.0, 1.0 - (time_window_seconds - self.config.max_impulse_duration_seconds) / 10.0)

        # 5. 综合置信度（加权平均）
        weights = {
            'price': 0.3,
            'volume': 0.25,
            'volatility': 0.25,
            'direction': 0.1,
            'time': 0.1
        }

        confidence = (
            price_score * weights['price'] +
            volume_score * weights['volume'] +
            volatility_score * weights['volatility'] +
            directional_consistency * weights['direction'] +
            time_score * weights['time']
        )

        return min(1.0, max(0.0, confidence))

    def _start_new_wave(self, tick: NormalizedTick, wave_result: Dict) -> None:
        """开始新的脉冲波"""
        self.current_wave = ImpulseWave(
            start_time=time.time(),
            start_price=tick.px,
            max_price=tick.px,
            min_price=tick.px,
            direction=wave_result['direction'],
            price_change_pct=wave_result['price_change_pct'],
            volatility_score=wave_result['volatility_ratio'],
            volume_score=wave_result['volume_multiplier'],
            confidence=wave_result['confidence'],
            is_active=True
        )

        # 更新成交量
        self.current_wave.total_volume += tick.sz
        self.current_wave.net_volume += tick.sz if tick.side == 1 else -tick.sz

        logger.debug(f"开始新的脉冲波: {self.current_wave}")

    def _update_current_wave(self, tick: NormalizedTick, wave_result: Dict) -> Optional[ImpulseWave]:
        """
        更新当前脉冲波

        Args:
            tick: 当前Tick
            wave_result: 检测结果

        Returns:
            如果脉冲波结束，返回完成的脉冲波对象；否则返回None
        """
        if not self.current_wave:
            return None

        # 更新价格范围
        self.current_wave.max_price = max(self.current_wave.max_price, tick.px)
        self.current_wave.min_price = min(self.current_wave.min_price, tick.px)

        # 更新成交量
        self.current_wave.total_volume += tick.sz
        self.current_wave.net_volume += tick.sz if tick.side == 1 else -tick.sz

        # 检查脉冲波是否结束
        wave_duration = time.time() - self.current_wave.start_time
        is_expired = wave_duration > self.config.max_impulse_duration_seconds

        # 检查脉冲波是否被破坏（价格反向运动）
        current_price = tick.px
        if self.current_wave.direction == ImpulseWaveDirection.BULLISH:
            is_broken = current_price < self.current_wave.start_price * 0.995  # 回撤超过0.5%
        else:
            is_broken = current_price > self.current_wave.start_price * 1.005  # 回撤超过0.5%

        # 检查置信度是否下降
        confidence_low = wave_result['confidence'] < 0.4

        # 结束脉冲波的条件
        if is_expired or is_broken or confidence_low or not wave_result['is_impulse']:
            completed_wave = self._complete_current_wave(tick)
            return completed_wave

        return None

    def _complete_current_wave(self, final_tick: NormalizedTick) -> ImpulseWave:
        """完成当前脉冲波"""
        if not self.current_wave:
            raise RuntimeError("没有当前脉冲波可以完成")

        # 设置结束信息
        self.current_wave.end_time = time.time()
        self.current_wave.end_price = final_tick.px
        self.current_wave.is_active = False

        # 计算最终价格变化百分比
        if self.current_wave.start_price > 0:
            price_change = ((self.current_wave.end_price - self.current_wave.start_price) /
                            self.current_wave.start_price * 100)
            self.current_wave.price_change_pct = price_change

        # 记录到历史
        self.wave_history.append(self.current_wave)
        self.stats['waves_detected'] += 1

        # 更新统计信息
        wave_duration = self.current_wave.end_time - self.current_wave.start_time
        self.stats['avg_wave_duration_seconds'] = (
            (self.stats['avg_wave_duration_seconds'] * (self.stats['waves_detected'] - 1) + wave_duration) /
            self.stats['waves_detected']
        )
        self.stats['avg_price_change_pct'] = (
            (self.stats['avg_price_change_pct'] * (self.stats['waves_detected'] - 1) +
             self.current_wave.price_change_pct) /
            self.stats['waves_detected']
        )
        self.stats['avg_confidence'] = (
            (self.stats['avg_confidence'] * (self.stats['waves_detected'] - 1) +
             self.current_wave.confidence) /
            self.stats['waves_detected']
        )

        completed_wave = self.current_wave
        logger.debug(f"脉冲波结束: {completed_wave}")

        # 重置当前脉冲波
        self.current_wave = None

        return completed_wave

    def get_current_wave(self) -> Optional[ImpulseWave]:
        """获取当前活跃的脉冲波"""
        return self.current_wave

    def get_recent_waves(self, count: int = 10) -> List[ImpulseWave]:
        """获取最近的脉冲波历史"""
        return self.wave_history[-count:] if self.wave_history else []

    def is_in_impulse_wave(self) -> bool:
        """检查当前是否处于脉冲波中"""
        return self.current_wave is not None and self.current_wave.is_active

    def get_impulse_wave_metrics(self) -> Dict:
        """获取脉冲波检测器指标"""
        return {
            'is_in_impulse_wave': self.is_in_impulse_wave(),
            'current_wave': self.current_wave,
            'stats': self.stats.copy(),
            'buffer_size': len(self.tick_buffer),
            'avg_volume': self.avg_volume,
            'historical_volatility': self.historical_volatility
        }


# 测试函数
def test_impulse_wave_detection():
    """测试脉冲波检测功能"""
    from src.strategy.triplea.core.data_structures import KDEEngineConfig

    logger.info("🔬 脉冲波检测器测试开始")

    # 创建配置
    config = KDEEngineConfig()
    detector = ImpulseWaveDetector(config)

    # 模拟数据：先横盘，然后脉冲上涨
    import random
    base_price = 3000.0
    current_price = base_price

    # 横盘阶段（100个tick）
    for i in range(100):
        tick = NormalizedTick(
            ts=int(time.time() * 1e9) + i * 100_000_000,  # 每0.1秒一个tick
            px=current_price + random.uniform(-0.5, 0.5),
            sz=random.uniform(0.1, 0.5),
            side=random.choice([1, -1])
        )
        detector.process_tick(tick)

    # 脉冲上涨阶段（20个tick）
    logger.info("开始模拟脉冲上涨...")
    for i in range(20):
        # 价格上涨，成交量放大
        current_price += 1.5  # 每tick上涨1.5美元
        tick = NormalizedTick(
            ts=int(time.time() * 1e9) + (100 + i) * 100_000_000,
            px=current_price,
            sz=random.uniform(1.0, 3.0),  # 成交量放大
            side=1  # 主动买入
        )
        completed_wave = detector.process_tick(tick)
        if completed_wave:
            logger.info(f"检测到脉冲波结束: {completed_wave}")

    # 检查结果
    metrics = detector.get_impulse_wave_metrics()
    logger.info(f"检测器指标: {metrics}")

    recent_waves = detector.get_recent_waves()
    if recent_waves:
        logger.info(f"检测到 {len(recent_waves)} 个脉冲波")
        for i, wave in enumerate(recent_waves):
            logger.info(f"  脉冲波 {i+1}: {wave}")
    else:
        logger.warning("未检测到脉冲波")

    return detector


if __name__ == "__main__":
    # 运行测试
    test_impulse_wave_detection()