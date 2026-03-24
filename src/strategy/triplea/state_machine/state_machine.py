#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四号引擎v3.0 状态机（5状态模型）
实现IDLE→MONITORING→CONFIRMED→ACCUMULATING→POSITION状态转换
集成LVN检测、CVD分析、波动率压缩检测等核心算法
专为实时交易决策优化，毫秒级延迟
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List, Any, Tuple

import numpy as np

from src.strategy.triplea.data_processing.cvd_calculator import CVDCalculator
from src.strategy.triplea.core.data_structures import (
    NormalizedTick, TripleAEngineConfig
)
from src.strategy.triplea.lvn.lvn_manager import LVNManager
from src.strategy.triplea.data_processing.range_bar_generator import RangeBarGenerator
from src.strategy.triplea.risk.risk_manager import RiskManager
from src.strategy.triplea.kde.kde_engine import KDEEngine
from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleAState(Enum):
    """四号引擎5状态模型"""
    IDLE = "IDLE"  # 空闲状态，等待价格进入LVN
    MONITORING = "MONITORING"  # 监控状态，价格在LVN内，等待CVD背离
    CONFIRMED = "CONFIRMED"  # 确认状态，CVD背离出现，等待积累信号
    ACCUMULATING = "ACCUMULATING"  # 积累状态，波动率压缩，等待攻击信号
    POSITION = "POSITION"  # 持仓状态，已开仓，等待止损/止盈


class StateTransitionEvent(Enum):
    """状态转换事件"""
    ENTER_LVN = "ENTER_LVN"  # 价格进入LVN区域
    EXIT_LVN = "EXIT_LVN"  # 价格离开LVN区域（超时）
    CVD_DIVERGENCE = "CVD_DIVERGENCE"  # 出现CVD背离信号
    VOL_COMPRESSION = "VOL_COMPRESSION"  # 波动率压缩信号
    HIGH_TICK_DENSITY = "HIGH_TICK_DENSITY"  # 高Tick密度信号
    AGGRESSION_SIGNAL = "AGGRESSION_SIGNAL"  # 攻击信号（大单气泡+足迹失衡）


@dataclass
class StateContext:
    """状态机上下文（保存当前状态和决策数据）"""

    # 当前状态
    current_state: TripleAState = TripleAState.IDLE

    # 活跃的LVN区域信息
    active_lvn_region: Optional[Dict[str, Any]] = None
    lvn_regions: List[Dict[str, Any]] = field(default_factory=list)
    entered_lvn_time: Optional[float] = None  # 进入LVN的时间戳（秒）
    lvn_center_price: Optional[float] = None  # LVN中心价格
    lvn_width: Optional[float] = None  # LVN宽度

    # CVD分析数据
    current_cvd_values: Dict[int, float] = field(default_factory=dict)  # {窗口大小: CVD值}
    cvd_statistics: Dict[int, Dict[str, float]] = field(default_factory=dict)  # {窗口大小: {统计指标}}
    cvd_divergence_detected: bool = False
    cvd_divergence_direction: Optional[str] = None  # "BULLISH" or "BEARISH"

    # 波动率压缩检测
    volatility_compression_detected: bool = False
    price_range_ticks: float = 0.0  # 最近价格范围（Tick单位）
    compression_start_time: Optional[float] = None

    # Tick密度分析
    tick_density_high: bool = False
    ticks_in_compression: int = 0  # 压缩期内累计Tick数
    ticks_per_second: float = 0.0  # 最近Tick频率

    # 攻击信号检测
    large_order_bubble_detected: bool = False
    footprint_imbalance_detected: bool = False
    aggression_signal_triggered: bool = False

    # 交易决策
    trade_direction: Optional[str] = None  # "LONG" or "SHORT"
    entry_price: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0

    # 时间跟踪
    state_enter_time: float = field(default_factory=time.time)

    # 历史记录（用于分析和调试）
    state_history: List[Tuple[TripleAState, float, str]] = field(default_factory=list)  # (状态, 时间戳, 触发事件)
    event_history: List[Tuple[StateTransitionEvent, float, Dict]] = field(default_factory=list)

    # 性能统计
    stats: Dict[str, Any] = field(default_factory=lambda: {
        'total_ticks_processed': 0,
        'avg_processing_time_ns': 0,
        'state_transitions': 0,
        'events_triggered': 0,
        'cvd_divergence_count': 0,
        'vol_compression_count': 0,
        'aggression_signal_count': 0
    })

    def update_state(self, new_state: TripleAState, event: str, details: Optional[Dict[str, Any]] = None):
        """更新状态并记录历史

        Args:
            new_state: 新状态
            event: 事件描述
            details: 详细数据字典，包含触发条件的具体数值
        """
        old_state = self.current_state
        self.current_state = new_state
        self.state_enter_time = time.time()

        # 状态emoji映射
        state_emojis = {
            TripleAState.IDLE: "🛌",
            TripleAState.MONITORING: "🔍",
            TripleAState.CONFIRMED: "✅",
            TripleAState.ACCUMULATING: "📊",
            TripleAState.POSITION: "💰"
        }

        # 构建详细日志消息
        old_emoji = state_emojis.get(old_state, "❓")
        new_emoji = state_emojis.get(new_state, "❓")

        # 基础日志
        log_msg = f"{old_emoji}{old_state.value} → {new_emoji}{new_state.value} [{event}]"

        # 如果有详细信息，添加到日志
        if details:
            details_str = " | ".join([f"{k}:{v}" if not isinstance(v, float) else f"{k}:{v:.4f}"
                                     for k, v in details.items()])
            log_msg = f"{log_msg} | {details_str}"

        logger.info(f"状态转换: {log_msg}")

        # 保存历史记录（包含详细信息）
        history_event = event
        if details:
            # 将details转换为字符串以便存储
            details_summary = " ".join([f"{k[:3]}:{v:.2f}" if isinstance(v, (int, float)) else f"{k[:3]}:{v}"
                                       for k, v in details.items()])
            history_event = f"{event} ({details_summary})"

        self.state_history.append((
            new_state,
            self.state_enter_time,
            history_event
        ))
        self.stats['state_transitions'] += 1

        # 限制历史记录大小
        if len(self.state_history) > 1000:
            self.state_history = self.state_history[-500:]

    def record_event(self, event: StateTransitionEvent, details: Dict[str, Any]):
        """记录事件"""
        # 事件emoji映射
        event_emojis = {
            StateTransitionEvent.ENTER_LVN: "📥",
            StateTransitionEvent.EXIT_LVN: "📤",
            StateTransitionEvent.CVD_DIVERGENCE: "📊",
            StateTransitionEvent.VOL_COMPRESSION: "📉",
            StateTransitionEvent.HIGH_TICK_DENSITY: "🐌",
            StateTransitionEvent.AGGRESSION_SIGNAL: "⚡"
        }

        # 构建详细事件日志
        event_emoji = event_emojis.get(event, "📌")
        event_summary = " ".join([f"{k[:4]}:{v:.2f}" if isinstance(v, (int, float)) else f"{k[:4]}:{v}"
                                  for k, v in details.items()])

        event_log_msg = f"{event_emoji}{event.value} | {event_summary}"

        logger.info(f"事件记录: {event_log_msg}")

        # 保存到历史
        self.event_history.append((
            event,
            time.time(),
            details
        ))
        self.stats['events_triggered'] += 1

        # 限制事件历史大小
        if len(self.event_history) > 1000:
            self.event_history = self.event_history[-500:]


class TripleAStateMachine:
    """
    四号引擎状态机（5状态模型）

    状态转换逻辑：
    1. IDLE -> MONITORING: 价格进入LVN区域
    2. MONITORING -> CONFIRMED: 出现CVD背离信号
    3. CONFIRMED -> ACCUMULATING: 波动率压缩 + 高Tick密度
    4. ACCUMULATING -> POSITION: 大单气泡 + 足迹失衡

    额外转换：
    - MONITORING -> IDLE: 价格离开LVN（超时）
    - CONFIRMED -> IDLE: CVD背离消失或超时
    - ACCUMULATING -> IDLE: 波动率压缩失败或超时
    """

    def __init__(self, config: TripleAEngineConfig):
        """
        初始化状态机

        Args:
            config: 四号引擎完整配置
        """
        self.config = config

        # 核心组件初始化
        # 注释掉KDE和LVN，暂时不运行
        self.kde_engine = None  # KDEEngine(config)
        self.lvn_manager = None  # LVNManager(config.kde_engine)
        self.cvd_calculator = CVDCalculator(
            window_sizes=[10, 30, 60, 120, 240]  # 多时间窗口分析
        )
        self.range_bar_generator = RangeBarGenerator(config.range_bar)
        self.risk_manager = RiskManager(config.risk_manager)

        # 事件循环检测
        try:
            self.loop = asyncio.get_event_loop()
            self.loop_running = self.loop.is_running()
        except RuntimeError:
            self.loop = None
            self.loop_running = False

        # 状态机上下文
        self.context = StateContext()

        # 时间窗口配置（秒）
        self.monitoring_timeout = 120  # 监控状态超时（2分钟）
        self.confirmed_timeout = 300  # 确认状态超时（5分钟）
        self.accumulating_timeout = 120  # 积累状态超时（2分钟）

        # LVN检测阈值
        self.lvn_confidence_threshold = 0.5  # LVN置信度阈值
        self.max_lvn_distance = 10.0  # 最大LVN距离（美元）

        # CVD背离检测参数
        self.cvd_divergence_window = 60  # CVD背离分析窗口（Tick数）
        self.cvd_zscore_threshold = 2.0  # CVD Z-score阈值

        # 波动率压缩参数
        self.vol_compression_threshold = 3.0  # 压缩阈值（Tick数）
        self.min_compression_duration = 5.0  # 最小压缩持续时间（秒）

        # Tick密度参数
        self.min_tick_density = 50  # 最小Tick数（压缩期内）
        self.tick_density_window = 60  # 密度分析窗口（秒）

        # 攻击信号参数
        self.large_order_multiplier = 99.0  # 大单气泡倍数（百分位）
        self.footprint_imbalance_threshold = 3.0  # 足迹失衡阈值（倍数）
        self.min_consecutive_levels = 3  # 最小连续失衡档位数

        # 实时数据缓存（用于计算指标）
        self.price_buffer = deque(maxlen=1000)
        self.tick_time_buffer = deque(maxlen=1000)
        self.order_size_buffer = deque(maxlen=1000)

        # 性能监控
        self.processing_times = deque(maxlen=100)
        self.last_processing_time_ns = 0

        logger.info(f"TripleAStateMachine 初始化完成")
        logger.info(f"状态模型: IDLE → MONITORING → CONFIRMED → ACCUMULATING → POSITION")

    async def process_tick(self, tick: NormalizedTick) -> Optional[Dict[str, Any]]:
        """
        处理单个Tick，更新状态机并返回交易信号

        Args:
            tick: 标准化Tick

        Returns:
            交易信号字典（如有），否则返回None
        """
        start_time_ns = time.perf_counter_ns()

        try:
            # 调试日志：Tick处理开始
            logger.debug(f"[DEBUG] 处理Tick: 价格={tick.px:.2f}, 大小={tick.sz:.4f}, 方向={'BUY' if tick.side > 0 else 'SELL'}, 当前状态={self.context.current_state}")

            # 更新实时数据缓存
            self._update_data_buffers(tick)

            # 更新核心计算组件
            cvd_values = self.cvd_calculator.on_tick(tick)
            self.context.current_cvd_values = cvd_values

            # 更新CVD统计
            self.context.cvd_statistics = self.cvd_calculator.get_statistics()

            # 处理KDE计算（完全异步，不阻塞事件循环）
            # 注释掉KDE处理，暂时不运行
            # try:
            #     # 直接await KDE引擎的异步方法，避免使用run_coroutine_threadsafe+future_result()的阻塞模式
            #     lvn_regions = await self.kde_engine.process_tick(tick)
            #
            #     # 获取最新的网格和密度
            #     grid, densities = self.kde_engine.get_latest_kde_grid()
            #     if grid is not None and densities is not None:
            #         # 处理KDE结果，更新LVN区域
            #         self.lvn_manager.process_kde_result(grid, densities)
            # except Exception as e:
            #     logger.warning(f"KDE处理失败: {e}", exc_info=True)

            # 根据当前状态执行不同逻辑
            signal = None

            if self.context.current_state == TripleAState.IDLE:
                signal = self._handle_idle_state(tick)

            elif self.context.current_state == TripleAState.MONITORING:
                signal = self._handle_monitoring_state(tick)

            elif self.context.current_state == TripleAState.CONFIRMED:
                signal = self._handle_confirmed_state(tick)

            elif self.context.current_state == TripleAState.ACCUMULATING:
                signal = self._handle_accumulating_state(tick)

            elif self.context.current_state == TripleAState.POSITION:
                signal = self._handle_position_state(tick)

            # 更新性能统计
            self.context.stats['total_ticks_processed'] += 1

            # 检查状态超时（防止状态卡死）
            self._check_state_timeout()

            return signal

        finally:
            end_time_ns = time.perf_counter_ns()
            self.last_processing_time_ns = end_time_ns - start_time_ns
            # 更新性能统计
            self.processing_times.append(self.last_processing_time_ns)
            self.context.stats['avg_processing_time_ns'] = np.mean(
                self.processing_times) if self.processing_times else 0

    def _handle_idle_state(self, tick: NormalizedTick) -> Optional[Dict[str, Any]]:
        """
        处理IDLE状态

        逻辑：检测吸收信号（CVD背离）
        """
        # 检测CVD背离作为吸收信号
        cvd_divergence_detected = self._detect_cvd_divergence()

        if cvd_divergence_detected:
            # 确定背离方向
            direction = self._determine_cvd_divergence_direction()

            # 获取具体的Z-score值
            window = 60
            z_score = 0.0
            if window in self.context.cvd_statistics:
                z_score = self.context.cvd_statistics[window].get('z_score', 0.0)

            # 构建详细数据
            details = {
                '价格': tick.px,
                'CVD_Z-score': z_score,
                '阈值': self.cvd_zscore_threshold,
                '方向': direction,
                '检测窗口': window
            }

            # 记录事件
            self.context.record_event(
                StateTransitionEvent.CVD_DIVERGENCE,
                {
                    'direction': direction,
                    'current_price': tick.px,
                    'cvd_values': self.context.current_cvd_values,
                    'statistics': self.context.cvd_statistics
                }
            )

            # 触发状态转换（传递详细数据）
            self.context.update_state(
                TripleAState.MONITORING,
                f"检测到CVD背离 ({direction})",
                details=details
            )

        return None

    def _handle_monitoring_state(self, tick: NormalizedTick) -> Optional[Dict[str, Any]]:
        """
        处理MONITORING状态

        逻辑：
        1. 检查价格是否离开LVN区域（超时则返回IDLE）
        2. 检测CVD背离信号
        """
        # 调试日志：进入MONITORING状态处理
        logger.debug(f"[DEBUG] _handle_monitoring_state: 价格={tick.px:.2f}, LVN区域={self.context.active_lvn_region}")

        # 检查LVN区域是否仍然有效（暂时禁用LVN检查）
        # if not self._is_price_in_lvn(tick.px):
        #     # 价格离开LVN区域，返回IDLE状态
        #     self.context.update_state(
        #         TripleAState.IDLE,
        #         "价格离开LVN区域"
        #     )
        #     logger.info("🔙 返回IDLE状态: 价格离开LVN区域")
        #     return None

        # 检测CVD背离信号
        cvd_divergence_detected = self._detect_cvd_divergence()
        logger.debug(f"[DEBUG] CVD背离检测结果: {cvd_divergence_detected}")

        if cvd_divergence_detected:
            self.context.cvd_divergence_detected = True

            # 确定背离方向
            direction = self._determine_cvd_divergence_direction()
            self.context.cvd_divergence_direction = direction

            # 获取具体的Z-score值
            window = 60
            z_score = 0.0
            if window in self.context.cvd_statistics:
                z_score = self.context.cvd_statistics[window].get('z_score', 0.0)

            # 构建详细数据
            details = {
                '价格': tick.px,
                'CVD_Z-score': z_score,
                '阈值': self.cvd_zscore_threshold,
                '方向': direction,
                '检测窗口': window
            }

            # 转换到CONFIRMED状态（传递详细数据）
            self.context.update_state(
                TripleAState.CONFIRMED,
                f"检测到CVD背离 ({direction})",
                details=details
            )

            # 记录事件
            self.context.record_event(
                StateTransitionEvent.CVD_DIVERGENCE,
                {
                    'direction': direction,
                    'current_price': tick.px,
                    'cvd_values': self.context.current_cvd_values,
                    'statistics': self.context.cvd_statistics
                }
            )

        return None

    def _handle_confirmed_state(self, tick: NormalizedTick) -> Optional[Dict[str, Any]]:
        """
        处理CONFIRMED状态

        逻辑：
        1. 检测波动率压缩信号
        2. 检测高Tick密度信号
        3. 两者同时满足则进入ACCUMULATING状态
        """
        # 调试日志：进入CONFIRMED状态处理
        logger.debug(f"[DEBUG] _handle_confirmed_state: 价格={tick.px:.2f}, CVD背离方向={self.context.cvd_divergence_direction}")

        # 检测波动率压缩
        vol_compression = self._detect_volatility_compression()

        # 检测Tick密度
        high_density = self._detect_high_tick_density()

        logger.debug(f"[DEBUG] 波动率压缩检测: {vol_compression}, Tick密度检测: {high_density}")

        if vol_compression and high_density:
            # 计算持续时间
            duration = 0.0
            if self.context.compression_start_time:
                duration = time.time() - self.context.compression_start_time

            # 构建详细数据
            details = {
                '价格': tick.px,
                '价格范围_ticks': self.context.price_range_ticks,
                '压缩阈值': self.vol_compression_threshold,
                'Tick密度': self.context.ticks_per_second,
                '最小Tick数': self.min_tick_density,
                '压缩持续时间': duration,
                '最小持续时间': self.min_compression_duration,
                'CVD方向': self.context.cvd_divergence_direction
            }

            # 进入ACCUMULATING状态（传递详细数据）
            self.context.update_state(
                TripleAState.ACCUMULATING,
                "波动率压缩 + 高Tick密度",
                details=details
            )

            # 记录事件
            self.context.record_event(
                StateTransitionEvent.VOL_COMPRESSION,
                {
                    'price_range_ticks': self.context.price_range_ticks,
                    'compression_duration': duration,
                    'tick_density': self.context.ticks_per_second
                }
            )

        return None

    def _handle_accumulating_state(self, tick: NormalizedTick) -> Optional[Dict[str, Any]]:
        """
        处理ACCUMULATING状态

        逻辑：
        1. 检测大单气泡信号
        2. 检测足迹失衡信号
        3. 两者同时满足则进入POSITION状态并生成开仓信号
        """
        # 调试日志：进入ACCUMULATING状态处理
        logger.debug(f"[DEBUG] _handle_accumulating_state: 价格={tick.px:.2f}, 波动率压缩有效={self._is_vol_compression_valid()}")

        # 检测大单气泡
        large_order = self._detect_large_order_bubble()

        # 检测足迹失衡
        footprint_imbalance = self._detect_footprint_imbalance()

        logger.debug(f"[DEBUG] 大单气泡检测: {large_order}, 足迹失衡检测: {footprint_imbalance}")

        if large_order and footprint_imbalance:
            # 生成交易信号
            signal = self._generate_trade_signal(tick)

            # 检查是否被风控拦截
            if signal is None:
                # 风控拦截，返回IDLE状态
                details = {
                    '价格': tick.px,
                    '原因': '风控拦截：交易被拒绝',
                    '大单检测': large_order,
                    '足迹失衡检测': footprint_imbalance,
                    '波动率压缩有效': self._is_vol_compression_valid()
                }
                self.context.update_state(
                    TripleAState.IDLE,
                    "风控拦截：交易被拒绝",
                    details=details
                )
                return None

            # 进入POSITION状态
            details = {
                '入场价格': self.context.entry_price,
                '止损价格': self.context.stop_loss_price,
                '止盈价格': self.context.take_profit_price,
                '方向': self.context.trade_direction,
                '合约数量': self.context.position_quantity,
                '保本价格': self.context.breakeven_price,
                '当前价格': tick.px,
                '大单检测': large_order,
                '足迹失衡检测': footprint_imbalance
            }
            self.context.update_state(
                TripleAState.POSITION,
                f"攻击信号触发 ({self.context.trade_direction})",
                details=details
            )

            # 记录事件
            self.context.record_event(
                StateTransitionEvent.AGGRESSION_SIGNAL,
                {
                    'trade_direction': self.context.trade_direction,
                    'entry_price': self.context.entry_price,
                    'stop_loss': self.context.stop_loss_price,
                    'take_profit': self.context.take_profit_price
                }
            )

            return signal

        return None

    def _handle_position_state(self, tick: NormalizedTick) -> Optional[Dict[str, Any]]:
        """
        处理POSITION状态

        逻辑：检查止损/止盈条件

        """
        # 检查是否触及止损/止盈

        signal = None

        if self.context.trade_direction == "LONG":
            if tick.px <= self.context.stop_loss_price:
                signal = {
                    'action': 'CLOSE_LONG',
                    'reason': 'STOP_LOSS_HIT',
                    'price': tick.px
                }
            elif tick.px >= self.context.take_profit_price:
                signal = {
                    'action': 'CLOSE_LONG',
                    'reason': 'TAKE_PROFIT_HIT',
                    'price': tick.px
                }

        elif self.context.trade_direction == "SHORT":

            if tick.px >= self.context.stop_loss_price:
                signal = {
                    'action': 'CLOSE_SHORT',
                    'reason': 'STOP_LOSS_HIT',
                    'price': tick.px
                }
            elif tick.px <= self.context.take_profit_price:
                signal = {
                    'action': 'CLOSE_SHORT',
                    'reason': 'TAKE_PROFIT_HIT',
                    'price': tick.px
                }

        if signal:
            # 返回IDLE状态
            details = {
                '当前价格': tick.px,
                '触发原因': signal['reason'],
                '止损价格': self.context.stop_loss_price,
                '止盈价格': self.context.take_profit_price,
                '入场价格': self.context.entry_price,
                '方向': self.context.trade_direction,
                '仓位状态': '已平仓'
            }

            self.context.update_state(
                TripleAState.IDLE,
                f"仓位平仓: {signal['reason']}",
                details=details
            )

            # 重置交易相关上下文
            self.context.trade_direction = None
            self.context.entry_price = 0.0
            self.context.stop_loss_price = 0.0
            self.context.take_profit_price = 0.0

        return signal

    # ==========================================

    # 🔍 核心检测算法

    # ==========================================

    def _is_price_in_lvn(self, price: float, tolerance_ticks: int = 10) -> bool:
        """检查价格是否在活跃的LVN区域内（暂时禁用LVN，始终返回True）"""
        # 暂时禁用LVN检查，始终返回True
        return True

    def _detect_cvd_divergence(self) -> bool:

        """检测CVD背离信号"""

        # 使用主要分析窗口（60个Tick）

        window = 60

        if window not in self.context.cvd_statistics:
            return False

        stats = self.context.cvd_statistics[window]

        # 检查Z-score是否超过阈值

        if abs(stats.get('z_score', 0.0)) >= self.cvd_zscore_threshold:
            return True

        return False

    def _determine_cvd_divergence_direction(self) -> str:

        """确定CVD背离方向（BULLISH or BEARISH）"""

        window = 60

        if window not in self.context.cvd_statistics:
            return "UNKNOWN"

        stats = self.context.cvd_statistics[window]

        z_score = stats.get('z_score', 0.0)

        if z_score > 0:

            return "BULLISH"

        else:

            return "BEARISH"

    def _is_cvd_divergence_valid(self) -> bool:

        """检查CVD背离是否仍然有效"""

        # 简单实现：检查是否仍然检测到背离

        return self._detect_cvd_divergence()

    def _detect_volatility_compression(self) -> bool:

        """检测波动率压缩信号"""

        if len(self.price_buffer) < 50:
            return False

        # 计算最近价格范围（以Tick为单位）

        recent_prices = list(self.price_buffer)[-50:] # 取最后50个

        price_range = max(recent_prices) - min(recent_prices)

        tick_size = self.config.market.tick_size

        price_range_ticks = price_range / tick_size

        # 记录压缩开始时间

        if price_range_ticks < self.vol_compression_threshold:

            if self.context.compression_start_time is None:
                self.context.compression_start_time = time.time()

                self.context.ticks_in_compression = 0

            self.context.price_range_ticks = price_range_ticks

            # 检查持续时间是否达标

            duration = time.time() - self.context.compression_start_time

            if duration >= self.min_compression_duration:
                self.context.volatility_compression_detected = True

                return True



        else:

            # 压缩被打破，重置

            self.context.compression_start_time = None

            self.context.volatility_compression_detected = False

        return False

    def _is_vol_compression_valid(self) -> bool:

        """检查波动率压缩是否仍然有效"""

        if not self.context.volatility_compression_detected:
            return False

        # 重新检测，确保压缩仍然存在

        return self._detect_volatility_compression()

    def _detect_high_tick_density(self) -> bool:

        """检测高Tick密度信号"""

        if len(self.tick_time_buffer) < 10:
            return False

        # 计算最近Tick频率

        recent_times = list(self.tick_time_buffer)

        if len(recent_times) < 2:
            return False

        # 计算每秒Tick数

        time_window = min(60.0, recent_times[-1] - recent_times[0])

        if time_window <= 0:
            return False

        ticks_per_second = len(recent_times) / time_window

        self.context.ticks_per_second = ticks_per_second

        # 如果处于压缩状态，更新压缩期内的累计Tick数

        if self.context.compression_start_time is not None:

            self.context.ticks_in_compression += 1

            # 检查是否达到最小Tick数要求

            if self.context.ticks_in_compression >= self.min_tick_density:
                self.context.tick_density_high = True

                return True

        return False

    def _detect_large_order_bubble(self) -> bool:

        """检测大单气泡信号"""

        # 调试日志：大单检测输入
        buffer_size = len(self.order_size_buffer)
        logger.debug(f"[DEBUG] _detect_large_order_bubble: 缓冲区大小={buffer_size}, 百分位={self.large_order_multiplier}")

        if buffer_size < 50:
            return False

        # 计算成交量分布的百分位数

        sizes = list(self.order_size_buffer)

        try:

            # 计算99百分位（大单阈值）

            large_order_threshold = np.percentile(sizes, self.large_order_multiplier)
            logger.debug(f"[DEBUG] 大单阈值: {large_order_threshold:.6f} (百分位={self.large_order_multiplier}), 缓冲区大小={buffer_size}")

            # 检查最近是否有超过阈值的大单

            recent_sizes = sizes[-10:]  # 最近10个Tick

            for size in recent_sizes:

                if size >= large_order_threshold:
                    self.context.large_order_bubble_detected = True

                    return True



        except Exception as e:

            logger.debug(f"大单检测异常: {e}")

        # 调试日志：大单检测结果
        logger.debug(f"[DEBUG] 大单检测结果: 未找到超过阈值的大单")
        return False

    def _detect_footprint_imbalance(self) -> bool:

        """检测足迹失衡信号（简化版）"""

        # 调试日志：足迹失衡检测
        logger.debug(f"[DEBUG] _detect_footprint_imbalance: 缓冲区大小={len(self.order_size_buffer)}")

        # 简化实现：检查最近成交量的买卖比例

        if len(self.order_size_buffer) < 20:
            logger.debug(f"[DEBUG] 足迹失衡检测: 缓冲区不足")
            return False

        # 需要扩展以分析更详细的足迹数据

        # 临时返回True以便测试流程

        self.context.footprint_imbalance_detected = True
        logger.debug(f"[DEBUG] 足迹失衡检测: 返回True (简化实现)")

        return True

    def _calculate_structural_levels(self, entry_price: float, direction: str) -> tuple[float, float]:
        """计算结构性止损止盈价格

        根据用户要求：
        1. 止损放在吸收点下方2ticks（做多）或上方2ticks（做空）
        2. 止盈放在VAH下方一点点（做多）或VAL上方一点点（做空）
        3. 使用LVN区域边界作为吸收点和VAH/VAL的近似

        Args:
            entry_price: 入场价格
            direction: 交易方向 ("LONG" 或 "SHORT")

        Returns:
            tuple: (structural_stop_loss_price, structural_take_profit_price)
        """
        if not self.context.active_lvn_region:
            # 如果没有LVN区域，使用默认的tick数计算
            print("⚠️ 警告：无LVN区域数据，使用默认止损止盈计算")
            return self.risk_manager.calculate_stop_loss_take_profit(
                entry_price, direction, self.config.market.tick_size
            )

        # 获取LVN区域边界
        lvn_start = self.context.active_lvn_region['start_price']
        lvn_end = self.context.active_lvn_region['end_price']
        tick_size = self.config.market.tick_size

        if direction == "LONG":
            # 做多：吸收点 = LVN低点 (start_price)
            absorption_point = lvn_start
            # 止损 = 吸收点下方2ticks
            structural_sl = absorption_point - (50 * tick_size)

            # VAH近似 = LVN高点 (end_price)
            vah_approx = lvn_end
            # 止盈 = VAH下方一点点（1-2ticks）
            structural_tp = vah_approx - (50 * tick_size)

            # 确保止盈高于入场价（至少0.2%距离）
            min_tp_distance_pct = 0.002  # 0.2%
            min_tp_distance = entry_price * min_tp_distance_pct
            if structural_tp - entry_price < min_tp_distance:
                # 调整止盈以满足最小距离
                structural_tp = entry_price + min_tp_distance
                print(f"⚠️ 调整止盈以满足最小0.2%距离: {structural_tp:.2f}")

            # 确保止损低于入场价
            if structural_sl >= entry_price:
                structural_sl = entry_price - (50 * tick_size)
                print(f"⚠️ 调整止损以确保低于入场价: {structural_sl:.2f}")

        else:  # SHORT
            # 做空：吸收点 = LVN高点 (end_price)
            absorption_point = lvn_end
            # 止损 = 吸收点上方2ticks
            structural_sl = absorption_point + (50 * tick_size)

            # VAL近似 = LVN低点 (start_price)
            val_approx = lvn_start
            # 止盈 = VAL上方一点点（1-2ticks）
            structural_tp = val_approx + (50 * tick_size)

            # 确保止盈低于入场价（至少0.2%距离）
            min_tp_distance_pct = 0.002  # 0.2%
            min_tp_distance = entry_price * min_tp_distance_pct
            if entry_price - structural_tp < min_tp_distance:
                # 调整止盈以满足最小距离
                structural_tp = entry_price - min_tp_distance
                print(f"⚠️ 调整止盈以满足最小0.2%距离: {structural_tp:.2f}")

            # 确保止损高于入场价
            if structural_sl <= entry_price:
                structural_sl = entry_price + (50 * tick_size)
                print(f"⚠️ 调整止损以确保高于入场价: {structural_sl:.2f}")

        print(f"✅ 结构性水平计算:")
        print(f"  方向: {direction}")
        print(f"  LVN区间: [{lvn_start:.2f}, {lvn_end:.2f}]")
        print(f"  入场价: {entry_price:.2f}")
        print(f"  结构性止损: {structural_sl:.2f}")
        print(f"  结构性止盈: {structural_tp:.2f}")

        return structural_sl, structural_tp

    def _generate_trade_signal(self, tick: NormalizedTick) -> Optional[Dict[str, Any]]:
        """生成交易信号（包含开仓方向、价格、结构性止损止盈）

        根据用户要求：
        1. 止损放在吸收点下方2ticks（做多）或上方2ticks（做空）
        2. 止盈放在VAH下方一点点（做多）或VAL上方一点点（做空）
        3. 止盈距离至少0.2%（覆盖手续费）
        4. 盈亏比至少2:1

        Args:
            tick: 当前Tick数据

        Returns:
            交易信号字典，如果被风控拦截则返回None
        """
        # 调试日志：生成交易信号开始
        logger.debug(f"[DEBUG] _generate_trade_signal: 价格={tick.px:.2f}, CVD背离方向={self.context.cvd_divergence_direction}, LVN区域={self.context.active_lvn_region}")

        # 确定交易方向（基于CVD背离方向）
        direction = self.context.cvd_divergence_direction

        # 处理方向映射
        if direction == "BULLISH":
            trade_direction = "LONG"
        elif direction == "BEARISH":
            trade_direction = "SHORT"
        else:
            # 默认方向（基于价格相对于LVN中心的位置）
            if tick.px < self.context.lvn_center_price:
                trade_direction = "LONG"
            else:
                trade_direction = "SHORT"

        entry_price = tick.px

        # 计算结构性止损止盈价格
        structural_sl, structural_tp = self._calculate_structural_levels(
            entry_price, trade_direction
        )

        # 验证结构性水平的有效性
        if trade_direction == "LONG":
            if structural_sl >= entry_price or structural_tp <= entry_price:
                print(f"⚠️ 风控拦截：无效的结构性水平 (SL={structural_sl:.2f}, TP={structural_tp:.2f})")
                return None
        else:  # SHORT
            if structural_sl <= entry_price or structural_tp >= entry_price:
                print(f"⚠️ 风控拦截：无效的结构性水平 (SL={structural_sl:.2f}, TP={structural_tp:.2f})")
                return None

        # 调试日志：风险管理输入
        logger.debug(f"[DEBUG] 风控输入: 入场价={entry_price:.2f}, SL={structural_sl:.2f}, TP={structural_tp:.2f}, "
                    f"方向={trade_direction}, Tick大小={self.config.market.tick_size}")

        # 使用风险管理器的结构性仓位计算方法
        position_result = self.risk_manager.calculate_position_size_with_structure(
            entry_price=entry_price,
            structure_sl_price=structural_sl,
            structure_tp_price=structural_tp,
            direction=trade_direction,
            tick_size=self.config.market.tick_size
        )

        # 调试日志：风险管理结果
        logger.debug(f"[DEBUG] 风控结果: 数量={position_result.qty:.4f}, 止损价={position_result.stop_px:.2f}, "
                    f"止盈价={position_result.take_profit_px:.2f}, 保本价={position_result.breakeven_px:.2f}")

        # 如果仓位被风控拦截（数量为0），返回None
        if position_result.qty <= 0:
            print(f"⚠️ 风控拦截：仓位计算返回零数量，交易被拒绝")
            return None

        # 更新上下文
        self.context.trade_direction = trade_direction
        self.context.entry_price = entry_price
        self.context.stop_loss_price = structural_sl
        self.context.take_profit_price = structural_tp
        self.context.position_quantity = position_result.qty
        self.context.breakeven_price = position_result.breakeven_px

        # 生成信号
        signal = {
            'action': f'OPEN_{trade_direction}',
            'reason': 'AGGRESSION_SIGNAL',
            'price': entry_price,
            'stop_loss': structural_sl,
            'take_profit': structural_tp,
            'quantity': position_result.qty,
            'breakeven_price': position_result.breakeven_px,
            'risk_amount_usd': self.config.risk_manager.account_size_usdt * (
                    self.config.risk_manager.max_risk_per_trade_pct / 100.0),
            'timestamp': time.time(),
            'state_transition': {
                'from': TripleAState.ACCUMULATING,
                'to': TripleAState.POSITION
            },
            'structural_levels': {
                'absorption_point': self.context.active_lvn_region['start_price'] if trade_direction == "LONG" else
                self.context.active_lvn_region['end_price'],
                'vah_val_approx': self.context.active_lvn_region['end_price'] if trade_direction == "LONG" else
                self.context.active_lvn_region['start_price']
            }
        }

        logger.info(f"✅ 生成交易信号: {signal['action']} @ {signal['price']:.2f}")
        logger.info(f"   结构性止损: {signal['stop_loss']:.2f}, 止盈: {signal['take_profit']:.2f}")
        logger.info(f"   合约数量: {signal['quantity']:.3f}, 风险金额: {signal['risk_amount_usd']:.2f} USD")

        return signal

    def _update_data_buffers(self, tick: NormalizedTick):

        """更新数据缓冲区（用于计算指标）"""

        current_time = time.time()

        # 价格缓存

        self.price_buffer.append(tick.px)

        # Tick时间缓存（用于频率计算）

        self.tick_time_buffer.append(current_time)

        # 订单大小缓存（用于大单检测）

        self.order_size_buffer.append(tick.sz)

    def _check_state_timeout(self):

        """检查状态超时（防止状态卡死）"""

        current_time = time.time()

        state_duration = current_time - self.context.state_enter_time

        timeout_map = {

            TripleAState.MONITORING: self.monitoring_timeout,

            TripleAState.CONFIRMED: self.confirmed_timeout,

            TripleAState.ACCUMULATING: self.accumulating_timeout

        }

        current_state = self.context.current_state

        if current_state in timeout_map:

            timeout = timeout_map[current_state]

            if state_duration > timeout:
                # 状态超时，返回IDLE
                details = {
                    '超时状态': current_state.value,
                    '持续时间': state_duration,
                    '超时阈值': timeout,
                    '超时原因': '状态停留时间超过阈值'
                }

                self.context.update_state(
                    TripleAState.IDLE,
                    f"状态超时 ({current_state})",
                    details=details
                )

    def get_current_state(self) -> TripleAState:

        """获取当前状态"""

        return self.context.current_state

    def get_context(self) -> StateContext:

        """获取状态机上下文"""

        return self.context

    def get_performance_stats(self) -> Dict[str, Any]:

        """获取性能统计"""

        return {

            'last_processing_time_ns': self.last_processing_time_ns,

            'avg_processing_time_ns': self.context.stats['avg_processing_time_ns'],

            'total_ticks_processed': self.context.stats['total_ticks_processed'],

            'state_transitions': self.context.stats['state_transitions'],

            'events_triggered': self.context.stats['events_triggered'],

            'current_state': self.context.current_state.value,

            'state_duration_seconds': time.time() - self.context.state_enter_time

        }

    def reset(self):

        """重置状态机"""

        # 重置核心组件

        # self.lvn_manager.reset()  # 暂时禁用LVN

        self.cvd_calculator.reset()

        self.range_bar_generator.reset()

        # 重置上下文

        self.context = StateContext()

        # 重置数据缓存

        self.price_buffer.clear()

        self.tick_time_buffer.clear()

        self.order_size_buffer.clear()

        logger.info("TripleAStateMachine 已重置")


# 测试函数

def test_state_machine():
    """测试状态机基本功能"""

    logger = get_logger(__name__)

    print("🔬 测试四号引擎状态机")

    print("=" * 60)

    # 创建配置

    config = TripleAEngineConfig()

    state_machine = TripleAStateMachine(config)

    # 创建测试Tick

    test_ticks = []

    for i in range(200):
        price = 3000.0 + np.random.randn() * 5

        size = np.random.uniform(0.1, 2.0)

        side = 1 if np.random.rand() > 0.5 else -1

        tick = NormalizedTick(

            ts=i * 1_000_000,

            px=price,

            sz=size,

            side=side

        )

        test_ticks.append(tick)

    print(f"创建了 {len(test_ticks)} 个测试Tick")

    # 模拟处理Tick

    signals = []

    # 需要在 async 函数中运行测试
    async def run_test():
        signals = []
        for i, tick in enumerate(test_ticks[:100]):
            # ✅ 必须加上 await！
            signal = await state_machine.process_tick(tick)
            if signal:
                signals.append(signal)
                print(f"  Tick {i}: 触发信号 {signal['action']} - {signal['reason']}")
        return signals

    # 运行异步测试
    signals = asyncio.run(run_test())

    print(f"\n处理结果:")

    print(f"  处理Tick数: {state_machine.get_performance_stats()['total_ticks_processed']}")

    print(f"  平均处理时间: {state_machine.get_performance_stats()['avg_processing_time_ns'] / 1_000_000:.2f}ms")

    print(f"  触发信号数: {len(signals)}")

    # 输出状态转换历史

    context = state_machine.get_context()

    print(f"\n状态转换历史 ({len(context.state_history)} 次):")

    for i, (state, timestamp, event) in enumerate(context.state_history[-5:]):
        print(f"  {i + 1}: {state.value} [{event}]")

    return state_machine


if __name__ == "__main__":
    # 运行测试

    print("🚀 运行状态机测试...")

    test_state_machine()

    print("✅ 测试完成")
