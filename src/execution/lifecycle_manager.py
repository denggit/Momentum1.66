#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生命周期管理器 (LifecycleManager)
负责订单生命周期的4阶段状态机管理，与MarketContext和OKXTrader协作。

设计原则：
1. 职责分离：API调用归OKXTrader，生命周期管理归LifecycleManager
2. 状态统一：使用MarketContext作为唯一状态源
3. 配置驱动：生命周期参数从OrderFlowConfig读取
4. 事件驱动：监听MarketContext变化，响应市场情报

生命周期4阶段：
1. 阶段0：等待TP1成交，准备保本
2. 阶段1：TP1成交后，止损上移至保本价 + 机械阶梯防守 + 隐形墙跟随
3. 阶段2：价格逼近TP2，启动吹哨机制 + 动能破冰检测
4. 阶段3：无限登月模式，基于K线形态动态拔高止损
"""
import asyncio
import threading
import time
from datetime import datetime
from typing import Optional, Dict, Any

from src.context.market_context import MarketContext
from src.execution.trader import ExecutionResult
from src.strategy.orderflow_config import OrderFlowConfig
from src.utils.log import get_logger

logger = get_logger(__name__)


class LifecycleManager:
    """订单生命周期管理器"""

    def __init__(self, trader, context: MarketContext, config: OrderFlowConfig):
        """
        初始化生命周期管理器

        Args:
            trader: OKXTrader实例（纯API执行层）
            context: MarketContext实例（状态存储）
            config: OrderFlowConfig实例（配置参数）
        """
        self.trader = trader
        self.context = context
        self.config = config

        # 生命周期参数（从配置读取）
        self.breakeven_pct = config.breakeven_pct  # 保本价上浮比例（考虑手续费）
        self.mech_step1_trigger_pct = config.mech_step1_trigger_pct  # 阶段1触发涨幅
        self.mech_step1_sl_pct = config.mech_step1_sl_pct  # 阶段1止损位置
        self.wall_sl_offset_pct = config.wall_sl_offset_pct  # 墙下偏移比例
        self.moonbag_warning_ratio = config.moonbag_warning_ratio  # 距离TP2的比例
        self.fallback_threshold_pct = config.fallback_threshold_pct  # 回落阈值
        self.min_move_pct = config.min_move_pct  # 最小移动距离比例
        self.moon_strong_candle_pct = config.moon_strong_candle_pct  # 强推力阳线阈值
        self.moon_sl_offset_pct = config.moon_sl_offset_pct  # 登月止损偏移

        # 监控间隔参数
        self.stage0_interval = config.stage0_interval  # 阶段0监控间隔
        self.stage1_interval = config.stage1_interval  # 阶段1监控间隔
        self.stage2_interval = config.stage2_interval  # 阶段2监控间隔
        self.stage3_interval = config.stage3_interval  # 阶段3监控间隔

        # 运行时状态
        self._is_running = False
        self._monitor_task = None
        self._current_stage = 0
        self._current_sl_algo_id = None
        self._current_sl_price = 0.0
        self._execution_result: Optional[ExecutionResult] = None

        # 阶段3无限登月模式专用状态
        self._moon_phase_highest_price: float = 0.0  # 阶段3期间的最高价
        self._moon_phase_sl_base_pct: float = 0.0  # 阶段3止损基准百分比（相对于入场价）
        self._moon_phase_initialized: bool = False  # 阶段3追踪是否已初始化

        # 线程安全
        self._lock = threading.RLock()

        logger.info(f"[LifecycleManager] 初始化完成，交易对: {self.trader.symbol}")

        # 注册事件监听器（不阻塞，不增加锁时间）
        self._setup_event_listeners()

    def _setup_event_listeners(self):
        """
        设置MarketContext事件监听器
        安全设计：事件回调在锁外异步执行，不阻塞主tick处理
        """
        # 监听隐形墙价格变化（低频事件）
        self.context.add_event_listener('of_wall_updated', self._on_of_wall_updated)

        # 监听空头挤压标志变化（低频事件）
        self.context.add_event_listener('of_squeeze_updated', self._on_of_squeeze_updated)

        # 监听持仓变化（极低频事件）
        self.context.add_event_listener('position_updated', self._on_position_updated)

        logger.debug("[LifecycleManager] 事件监听器注册完成")

    def _remove_event_listeners(self):
        """移除所有事件监听器"""
        try:
            self.context.remove_event_listener('of_wall_updated', self._on_of_wall_updated)
            self.context.remove_event_listener('of_squeeze_updated', self._on_of_squeeze_updated)
            self.context.remove_event_listener('position_updated', self._on_position_updated)
            logger.debug("[LifecycleManager] 事件监听器已移除")
        except Exception as e:
            logger.error(f"[LifecycleManager] 移除事件监听器失败: {e}")

    async def _on_of_wall_updated(self, event_data: Dict[str, Any]):
        """
        处理隐形墙价格变化事件
        注意：此回调在事件循环中异步执行，不阻塞主线程
        """
        # 🛡️ 线程安全地获取状态
        with self._lock:
            if not self._is_running:
                return
            current_stage = self._current_stage

        # 阶段1和阶段3需要立即响应隐形墙变化
        if current_stage in [1, 3]:
            logger.info(f"[LifecycleManager] 收到隐形墙更新事件: {event_data['new_price']:.2f}")

            # 立即触发阶段逻辑处理（跳过轮询等待）
            if current_stage == 1:
                await self._stage1_breakeven_defense()
            elif current_stage == 3:
                await self._stage3_infinite_moon()

    async def _on_of_squeeze_updated(self, event_data: Dict[str, Any]):
        """
        处理空头挤压标志变化事件
        注意：此回调在事件循环中异步执行，不阻塞主线程
        """
        new_flag = event_data['new_flag']

        # 🛡️ 线程安全地获取状态
        with self._lock:
            if not self._is_running:
                return
            current_stage = self._current_stage

        # 只有阶段2关心空头挤压标志
        if current_stage == 2 and new_flag:
            logger.warning("[LifecycleManager] 收到空头挤压警报事件，立即进入无限登月模式！")

            # 立即执行阶段2的空头挤压处理逻辑
            with self._lock:
                self._current_stage = 3

            # 触发阶段3处理
            await self._stage3_infinite_moon()

    async def _on_position_updated(self, event_data: Dict[str, Any]):
        """
        处理持仓变化事件（主要用于状态同步）
        注意：此回调在事件循环中异步执行，不阻塞主线程
        """
        new_position = event_data.get('new_position')
        is_in_position = event_data.get('is_in_position', False)

        logger.debug(
            f"[LifecycleManager] 收到持仓更新事件: 持仓状态={is_in_position}, 新持仓信息={new_position is not None}")

        # 🆕 检测到仓位消失（手动平仓或止损止盈触发）
        if not is_in_position and self._is_running:
            logger.warning(f"[LifecycleManager] 检测到仓位已消失，停止生命周期管理")

            # 尝试取消所有未完成的订单（尽力而为）
            try:
                await self._cancel_all_pending_orders()
            except Exception as e:
                logger.error(f"[LifecycleManager] 取消未完成订单失败: {e}")

            # 停止生命周期管理
            await self.stop_lifecycle()

    async def start_lifecycle(self, execution_result: ExecutionResult):
        """
        启动订单生命周期管理

        Args:
            execution_result: 交易执行结果
        """
        with self._lock:
            if self._is_running:
                logger.warning("[LifecycleManager] 生命周期管理已在运行中")
                return

            self._execution_result = execution_result
            self._current_stage = 0
            self._is_running = True

            # 更新MarketContext中的持仓信息
            self._update_context_position()

            # 启动监控任务
            self._monitor_task = asyncio.create_task(self._lifecycle_monitor())
            logger.info(f"[LifecycleManager] 启动生命周期管理，阶段{self._current_stage}")

    async def stop_lifecycle(self):
        """停止生命周期管理"""
        # 首先检查是否正在运行
        with self._lock:
            if not self._is_running:
                return

            self._is_running = False

        # 🛡️ 移除事件监听器（锁外执行，避免死锁）
        self._remove_event_listeners()

        # 停止监控任务
        with self._lock:
            if self._monitor_task and not self._monitor_task.done():
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass

        # 重置阶段3无限登月状态
        with self._lock:
            self._moon_phase_highest_price = 0.0
            self._moon_phase_sl_base_pct = 0.0
            self._moon_phase_initialized = False

        logger.info("[LifecycleManager] 生命周期管理已停止")

    async def _lifecycle_monitor(self):
        """生命周期主监控循环"""
        try:
            while self._is_running:
                with self._lock:
                    if not self._is_running:
                        break

                    current_stage = self._current_stage

                # 根据当前阶段处理逻辑
                await self._process_current_stage(current_stage)

                # 根据阶段选择监控间隔
                interval = self._get_stage_interval(current_stage)
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.info("[LifecycleManager] 监控任务被取消")
        except Exception as e:
            logger.error(f"[LifecycleManager] 监控循环异常: {e}")
            # 发生异常时停止生命周期管理
            await self.stop_lifecycle()

    async def _process_current_stage(self, stage: int):
        """处理当前阶段逻辑"""
        if stage == 0:
            await self._stage0_wait_tp1()
        elif stage == 1:
            await self._stage1_breakeven_defense()
        elif stage == 2:
            await self._stage2_moonbag_warning()
        elif stage == 3:
            await self._stage3_infinite_moon()
        else:
            logger.error(f"[LifecycleManager] 未知阶段: {stage}")

    async def _stage0_wait_tp1(self):
        """阶段0：等待TP1成交，准备保本"""
        if not self._execution_result or not self._execution_result.tp1_order_id:
            logger.error("[LifecycleManager] 阶段0: 缺少TP1订单ID")
            return

        try:
            # 查询TP1订单状态
            order_status = await self.trader.get_order_status(self._execution_result.tp1_order_id)

            if order_status == 'filled':
                logger.warning(f"[LifecycleManager] TP1已成交，进入阶段1")

                # 获取入场价和当前保本参数
                entry_price = self._execution_result.entry_price
                breakeven_pct = self.breakeven_pct
                logger.info(
                    f"[LifecycleManager] 入场价: {entry_price:.4f}, 保本比例: {breakeven_pct:.4f} ({breakeven_pct * 100:.2f}%)")

                # 计算保本价
                breakeven_price = self._calculate_breakeven_price()
                logger.info(
                    f"[LifecycleManager] 计算保本价: {entry_price:.4f} * (1 + {breakeven_pct:.4f}) = {breakeven_price:.4f}")

                # 移动止损到保本价
                success = await self._move_stop_loss(
                    breakeven_price,
                    self._execution_result.remaining_size or self._execution_result.position_size
                )

                if success:
                    # 更新阶段
                    with self._lock:
                        self._current_stage = 1

                    # 更新MarketContext中的持仓阶段
                    self._update_context_stage(1)

                    logger.info(f"[LifecycleManager] 止损已移至保本价: {breakeven_price:.4f}")
                else:
                    logger.error("[LifecycleManager] 移动止损到保本价失败，保持阶段0")

            elif order_status in ['canceled', 'mismatch']:
                logger.info("[LifecycleManager] TP1订单被取消或失效，停止生命周期管理")
                await self.stop_lifecycle()

        except Exception as e:
            logger.error(f"[LifecycleManager] 阶段0监控异常: {e}")

    async def _stage1_breakeven_defense(self):
        """阶段1：保本防御 + 机械阶梯防守 + 隐形墙跟随"""
        if not self._execution_result:
            return

        try:
            # 获取当前价格
            current_price = self._get_current_price()
            if current_price <= 0:
                return

            entry_price = self._execution_result.entry_price
            target_sl = self._current_sl_price

            # 1. 机械阶梯防守
            mech_trigger = entry_price * (1 + self.mech_step1_trigger_pct)
            mech_sl = entry_price * (1 + self.mech_step1_sl_pct)

            if current_price >= mech_trigger:
                target_sl = max(target_sl, mech_sl)

            # 2. 隐形墙跟随
            wall_price = self.context.get_of_wall()
            if wall_price > entry_price:
                wall_sl = wall_price * (1 - self.wall_sl_offset_pct)
                target_sl = max(target_sl, wall_sl)

            # 3. 检查是否需要移动止损
            min_move = entry_price * self.min_move_pct
            if target_sl > self._current_sl_price + min_move:
                logger.warning(f"[LifecycleManager] 防线推进！最新止损锚定至: {target_sl:.2f}")
                success = await self._move_stop_loss(
                    target_sl,
                    self._execution_result.remaining_size or self._execution_result.position_size
                )

                if success:
                    with self._lock:
                        self._current_sl_price = target_sl

            # 4. 检查是否进入阶段2（吹哨预警）
            moonbag_warning_price = self._calculate_moonbag_warning_price()
            if current_price >= moonbag_warning_price:
                logger.warning(f"[LifecycleManager] 哨声响起！现价({current_price:.2f})已逼近TP2，进入阶段2")
                with self._lock:
                    self._current_stage = 2
                self._update_context_stage(2)

        except Exception as e:
            logger.error(f"[LifecycleManager] 阶段1监控异常: {e}")

    async def _stage2_moonbag_warning(self):
        """阶段2：吹哨预警 + 动能破冰检测"""
        if not self._execution_result:
            return

        try:
            # 获取当前价格
            current_price = self._get_current_price()
            if current_price <= 0:
                return

            # 1. 检查动能破冰（空头挤压标志）
            if self.context.get_of_squeeze():
                logger.warning("[LifecycleManager] 动能破冰！空头爆仓踩踏，进入无限登月模式")

                # 撤销TP2订单
                if self._execution_result.tp2_order_id:
                    success = await self.trader.cancel_order(self._execution_result.tp2_order_id)
                    if success:
                        logger.info("[LifecycleManager] TP2止盈单已撤销")

                # 进入阶段3
                with self._lock:
                    self._current_stage = 3
                self._update_context_stage(3)
                return

            # 2. 检查冲高回落
            moonbag_warning_price = self._calculate_moonbag_warning_price()
            fallback_threshold = moonbag_warning_price * (1 - self.fallback_threshold_pct)

            if current_price < fallback_threshold:
                logger.info("[LifecycleManager] 冲高回落，退回阶段1")
                with self._lock:
                    self._current_stage = 1
                self._update_context_stage(1)

            # 3. 继续阶段1的防御逻辑（移动止损）
            await self._stage1_breakeven_defense()

        except Exception as e:
            logger.error(f"[LifecycleManager] 阶段2监控异常: {e}")

    async def _stage3_infinite_moon(self):
        """阶段3：无限登月模式，基于K线形态动态拔高止损"""
        if not self._execution_result:
            return

        try:
            # 获取当前价格
            current_price = self._get_current_price()
            if current_price <= 0:
                return

            # 获取5分钟K线数据
            klines = await self.trader.get_klines("5m", limit=15)
            if not klines:
                return

            # ==================== 阶段3无限登月动态追踪逻辑 ====================
            # 1. 初始化阶段3追踪状态
            if not self._moon_phase_initialized:
                entry_price = self._execution_result.entry_price
                # 计算当前止损相对于入场价的百分比
                if self._current_sl_price > 0 and entry_price > 0:
                    current_sl_pct = (self._current_sl_price / entry_price) - 1
                else:
                    # 默认从保本价开始（0.15%）
                    current_sl_pct = self.breakeven_pct

                with self._lock:
                    self._moon_phase_highest_price = current_price
                    self._moon_phase_sl_base_pct = max(current_sl_pct, 0.0)  # 确保非负
                    self._moon_phase_initialized = True

                logger.info(
                    f"[LifecycleManager] 阶段3动态追踪初始化 | "
                    f"当前价: {current_price:.2f} | "
                    f"入场价: {entry_price:.2f} | "
                    f"初始止损基准: {self._moon_phase_sl_base_pct * 100:.3f}%"
                )

            # 2. 更新阶段3最高价
            with self._lock:
                if current_price > self._moon_phase_highest_price:
                    self._moon_phase_highest_price = current_price
                    logger.debug(f"[LifecycleManager] 阶段3最高价刷新: {current_price:.2f}")

            # 3. 动态追踪止损：最高价每抬升0.4%，止损上移0.4%
            entry_price = self._execution_result.entry_price
            highest_pct = (self._moon_phase_highest_price / entry_price) - 1

            # 计算基于涨幅的动态止损价（初始为当前止损价）
            dynamic_sl = self._current_sl_price
            move_count = 0

            # 🚀 循环处理：最高价每抬升0.4%，止损基准就上移0.4%
            # 这样可以处理一次大幅上涨触发多次上移的情况
            while highest_pct >= self._moon_phase_sl_base_pct + 0.004:
                # 计算新的止损基准百分比
                new_sl_base_pct = self._moon_phase_sl_base_pct + 0.004

                # 计算基于涨幅的止损价
                price_based_sl = entry_price * (1 + new_sl_base_pct)
                dynamic_sl = max(dynamic_sl, price_based_sl)

                # 更新止损基准
                with self._lock:
                    old_base_pct = self._moon_phase_sl_base_pct
                    self._moon_phase_sl_base_pct = new_sl_base_pct

                move_count += 1
                logger.warning(
                    f"[LifecycleManager] 🚀 动态追踪触发 (第{move_count}次) | "
                    f"最高价涨幅: {highest_pct * 100:.3f}% | "
                    f"止损基准 {old_base_pct * 100:.3f}% → {new_sl_base_pct * 100:.3f}% | "
                    f"动态止损价: {price_based_sl:.2f}"
                )

                # 继续检查是否还需要上移
                # 注意：highest_pct 不变，但 sl_base_pct 已更新，继续循环检查

            if move_count > 0:
                logger.info(f"[LifecycleManager] 本轮动态追踪共触发 {move_count} 次止损上移")

            target_sl = max(dynamic_sl, self._current_sl_price)

            # 策略A：强推力阳线 + 确认阳线
            for i in range(2, 10):
                if i >= len(klines):
                    break

                k1_open = float(klines[i][1])
                k1_low = float(klines[i][3])
                k1_close = float(klines[i][4])

                if i - 1 >= len(klines):
                    break
                k2_open = float(klines[i - 1][1])
                k2_close = float(klines[i - 1][4])

                # k1必须是一根阳线，且实体高度 >= 强推力阈值
                k1_body_pct = (k1_close - k1_open) / k1_open
                if k1_body_pct >= self.moon_strong_candle_pct:
                    # k2必须是阳线（走完行情的确认线）
                    if k2_close > k2_open:
                        sl_a = k1_low * (1 - self.moon_sl_offset_pct)
                        target_sl = max(target_sl, sl_a)
                        break

            # 策略B：标准的5分钟Swing Low波段低点防守
            for i in range(3, 10):
                if i + 2 >= len(klines):
                    break

                lows = [float(klines[j][3]) for j in range(i - 2, i + 3)]
                if len(lows) == 5:
                    l0, l1, l2, l3, l4 = lows
                    if l2 < l0 and l2 < l1 and l2 < l3 and l2 < l4:
                        sl_b = l2 * (1 - self.moon_sl_offset_pct)
                        target_sl = max(target_sl, sl_b)
                        break

            # 策略C：隐形筹码墙防守
            wall_price = self.context.get_of_wall()
            if wall_price > 0:
                target_sl = max(target_sl, wall_price * (1 - self.wall_sl_offset_pct))

            # 检查是否需要移动止损
            min_move = self._execution_result.entry_price * self.min_move_pct
            if target_sl > self._current_sl_price + min_move:
                logger.warning(f"[LifecycleManager] 利润狂飙！最新防线极速拔高至: {target_sl:.2f}")
                success = await self._move_stop_loss(
                    target_sl,
                    self._execution_result.remaining_size or self._execution_result.position_size
                )

                if success:
                    with self._lock:
                        self._current_sl_price = target_sl

        except Exception as e:
            logger.error(f"[LifecycleManager] 阶段3监控异常: {e}")

    async def _move_stop_loss(self, trigger_price: float, size: float) -> bool:
        """
        移动止损线

        Args:
            trigger_price: 止损触发价格
            size: 合约张数

        Returns:
            bool: 是否成功
        """
        try:
            logger.info(
                f"[LifecycleManager] 准备移动止损线到: {trigger_price:.4f}, 当前止损单ID: {self._current_sl_algo_id}")

            # 取消旧止损单
            if self._current_sl_algo_id:
                logger.info(f"[LifecycleManager] 正在取消旧止损单: {self._current_sl_algo_id}")
                success = await self.trader.cancel_algo_order(self._current_sl_algo_id)
                if not success:
                    logger.warning(f"[LifecycleManager] 取消旧止损单失败，但继续创建新止损单")

            # 创建新止损单
            logger.info(f"[LifecycleManager] 创建新止损单，触发价: {trigger_price:.4f}, 数量: {size}")
            algo_id = await self.trader.create_stop_loss_order(size, trigger_price)

            if algo_id:
                with self._lock:
                    self._current_sl_algo_id = algo_id
                    self._current_sl_price = trigger_price
                logger.info(f"[LifecycleManager] 止损线移动成功! 新止损单ID: {algo_id}, 价格: {trigger_price:.4f}")
                return True
            else:
                logger.error("[LifecycleManager] 创建止损单失败，返回空ID")
                return False

        except Exception as e:
            logger.error(f"[LifecycleManager] 移动止损异常: {e}")
            return False

    def _calculate_breakeven_price(self) -> float:
        """计算保本价（考虑手续费）"""
        if not self._execution_result:
            return 0.0

        entry_price = self._execution_result.entry_price
        breakeven_price = round(entry_price * (1 + self.breakeven_pct), 2)

        # 调试日志
        logger.debug(
            f"[LifecycleManager] 保本价计算: entry={entry_price:.4f}, pct={self.breakeven_pct:.6f}, result={breakeven_price:.4f}")
        logger.debug(f"[LifecycleManager] 保本价相对入场价涨幅: {(breakeven_price / entry_price - 1) * 100:.4f}%")

        return breakeven_price

    def _calculate_moonbag_warning_price(self) -> float:
        """计算吹哨预警价格（距离TP2一定比例的位置）"""
        if not self._execution_result:
            return 0.0

        entry_price = self._execution_result.entry_price
        tp2_price = self._execution_result.tp2_price

        # 计算距离TP2的比例位置
        return round(entry_price + (tp2_price - entry_price) * self.moonbag_warning_ratio, 2)

    def _get_current_price(self) -> float:
        """从MarketContext获取当前价格"""
        return self.context.get_current_price()

    def _get_stage_interval(self, stage: int) -> float:
        """获取当前阶段的监控间隔"""
        intervals = {
            0: self.stage0_interval,
            1: self.stage1_interval,
            2: self.stage2_interval,
            3: self.stage3_interval
        }
        return intervals.get(stage, 2.0)

    def _update_context_position(self):
        """更新MarketContext中的持仓信息"""
        if not self._execution_result:
            return

        # 获取现有持仓的entry_time（如果存在）
        existing_position = self.context.get_position()
        entry_time = None
        if existing_position and existing_position.entry_time:
            entry_time = existing_position.entry_time
        else:
            entry_time = datetime.fromtimestamp(time.time())

        position_info = {
            "symbol": self._execution_result.symbol,
            "side": "long",
            "size": self._execution_result.position_size,
            "entry_price": self._execution_result.entry_price,
            "current_price": self._get_current_price(),
            "unrealized_pnl": 0.0,
            "leverage": self.config.leverage,
            "stop_loss_price": self._execution_result.local_low * (1 - self.config.sl_pct),
            "take_profit_price": self._execution_result.tp2_price,
            "initial_stop_loss": self._execution_result.local_low * (1 - self.config.sl_pct),
            "stage": self._current_stage,
            "stage_start_price": self._execution_result.entry_price,
            "entry_time": entry_time
        }

        self.context.update_position(position_info)

    def _update_context_stage(self, stage: int):
        """更新MarketContext中的持仓阶段"""
        position = self.context.get_position()
        if position:
            position.stage = stage
            position.stage_start_time = datetime.fromtimestamp(time.time())
            self.context.update_position(position)

    def get_current_stage(self) -> int:
        """获取当前阶段"""
        with self._lock:
            return self._current_stage

    async def _cancel_all_pending_orders(self):
        """
        取消所有未完成的订单（TP1、TP2、止损单）
        用于仓位消失时的清理工作
        """
        try:
            with self._lock:
                # 保存引用，避免竞争条件
                tp1_order_id = self._execution_result.tp1_order_id if self._execution_result else None
                tp2_order_id = self._execution_result.tp2_order_id if self._execution_result else None
                sl_algo_id = self._current_sl_algo_id

            cancelled_count = 0

            # 取消TP1订单（如果存在且未成交）
            if tp1_order_id:
                try:
                    success = await self.trader.cancel_order(tp1_order_id)
                    if success:
                        logger.info(f"[LifecycleManager] 已取消TP1订单: {tp1_order_id}")
                        cancelled_count += 1
                    else:
                        logger.warning(f"[LifecycleManager] TP1订单取消失败或已成交: {tp1_order_id}")
                except Exception as e:
                    logger.error(f"[LifecycleManager] 取消TP1订单异常: {e}")

            # 取消TP2订单（如果存在且未成交）
            if tp2_order_id:
                try:
                    success = await self.trader.cancel_order(tp2_order_id)
                    if success:
                        logger.info(f"[LifecycleManager] 已取消TP2订单: {tp2_order_id}")
                        cancelled_count += 1
                    else:
                        logger.warning(f"[LifecycleManager] TP2订单取消失败或已成交: {tp2_order_id}")
                except Exception as e:
                    logger.error(f"[LifecycleManager] 取消TP2订单异常: {e}")

            # 取消止损算法订单（如果存在）
            if sl_algo_id:
                try:
                    success = await self.trader.cancel_algo_order(sl_algo_id)
                    if success:
                        logger.info(f"[LifecycleManager] 已取消止损算法订单: {sl_algo_id}")
                        cancelled_count += 1
                    else:
                        logger.warning(f"[LifecycleManager] 止损算法订单取消失败或已触发: {sl_algo_id}")
                except Exception as e:
                    logger.error(f"[LifecycleManager] 取消止损算法订单异常: {e}")

            logger.info(f"[LifecycleManager] 清理完成，共取消 {cancelled_count} 个未完成订单")

        except Exception as e:
            logger.error(f"[LifecycleManager] 取消订单过程中发生异常: {e}")

    def is_running(self) -> bool:
        """检查是否正在运行"""
        with self._lock:
            return self._is_running
