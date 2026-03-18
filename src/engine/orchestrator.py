#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
订单流引擎编排器 (Orchestrator)
建立单向数据流，协调所有组件，替换 Engine3Commander。

设计原则：
1. 单向数据流：DataFeed → OrderFlowMath → SMCValidator → OrderFlowExecutor → LifecycleManager
2. 状态集中：MarketContext 作为唯一状态源
3. 组件解耦：各组件通过上下文和事件通信，而非直接依赖
4. 配置驱动：所有参数从 OrderFlowConfig 读取

数据流：
1. Tick数据 → MarketContext.update_tick() → OrderFlowMath.process_tick()
2. 信号数据 → MarketContext.update_signal() → SMCValidator.final_check()
3. 验证通过 → OrderFlowExecutor.execute_snipe() → LifecycleManager.start_lifecycle()
4. 生命周期 → MarketContext.update_position() → 持续监控

事件驱动：
- MarketContext 状态变化触发组件行为
- LifecycleManager 监听 of_wall_price 和 of_squeeze_flag 变化
- SMCValidator 定期更新结构，更新 MarketContext 中的 SMC 水平
"""
import argparse
import asyncio
import os
import signal
import sys
import time
from typing import Dict, Any

# 确保能导入项目根目录的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_stream import OKXTickStreamer
from src.strategy.orderflow.orderflow import OrderFlowMath
from src.strategy.orderflow.smc_validator import SMCValidator
from src.execution.orderflow_executor import OrderFlowExecutor
from src.execution.lifecycle_manager import LifecycleManager
from src.execution.trader import OKXTrader
from engines.engine_3_orderflow.tracker import CSVTracker
from src.context.market_context import MarketContext
from src.utils.log import get_logger
from src.utils.email_sender import send_trading_signal_email
from config.loader import load_orderflow_config

logger = get_logger(__name__)


class OrderFlowOrchestrator:
    """订单流引擎编排器"""

    def __init__(self, symbol: str = "ETH-USDT-SWAP", mode: str = "collect"):
        """
        初始化编排器

        Args:
            symbol: 交易对符号
            mode: 运行模式 ('collect' 或 'live')
        """
        self.symbol = symbol
        self.mode = mode

        # 加载配置
        try:
            self.config = load_orderflow_config(symbol)
            logger.info(f"✅ 成功加载 {symbol} 订单流配置")
        except Exception as e:
            logger.error(f"❌ 加载配置失败: {e}, 使用默认配置")
            from src.strategy.orderflow.orderflow_config import OrderFlowConfig
            self.config = OrderFlowConfig()

        # 创建线程安全的市场上下文 (核心状态存储)
        self.context = MarketContext()

        # ==================== 初始化核心组件 ====================

        # 1. 订单流数学大脑 (数据处理器)
        self.math_brain = OrderFlowMath(config=self.config, context=self.context)

        # 2. SMC 验证器 (宏观结构验证)
        self.smc_validator = SMCValidator(
            symbol=symbol,
            timeframes=self.config.smc_timeframes
        )

        # 3. 科考船追踪器 (信号记录)
        self.tracker = CSVTracker(project_root, context=self.context)

        # 4. 实盘交易器 (纯API层)
        self.trader = OKXTrader(
            symbol=symbol,
            leverage=self.config.leverage,
            risk_pct=self.config.risk_pct,
            sl_pct=self.config.sl_pct,
            context=self.context
        )

        # 5. OrderFlow 执行策略器 (三连发逻辑)
        self.executor = OrderFlowExecutor(trader=self.trader, config=self.config)

        # 6. 生命周期管理器 (4阶段止损管理)
        self.lifecycle_manager = LifecycleManager(
            trader=self.trader,
            context=self.context,
            config=self.config
        )

        # 7. 数据流连接器 (Tick数据源)
        self.streamer = OKXTickStreamer(
            symbol=symbol,
            on_tick_callback=self.on_tick
        )

        # ==================== 运行时状态 ====================

        self._last_email_sent_time = 0
        self._email_cooldown = self.config.email_cooldown
        self.last_intel_time = 0
        self._is_running = False

        logger.info(f"🚀 订单流编排器初始化完成: {symbol} [{mode.upper()}]")

    async def on_tick(self, tick: Dict[str, Any]):
        """
        核心数据流处理函数：接收Tick数据，驱动单向流水线

        数据流：Tick → Context → OrderFlowMath → Signal → SMC验证 → 执行 → 生命周期
        """
        # 0. 更新MarketContext中的最新Tick数据
        self.context.update_tick(tick)

        # 1. 订单流数学处理 (生成信号)
        signal_data = self.math_brain.process_tick(tick)

        # 2. 实时情报扫描 (隐形墙和空头挤压)
        self._scan_intelligence(tick)

        # 3. 信号分发处理
        if signal_data:
            await self._process_signal(signal_data, tick)

        # 4. 科考船更新 (最高价和止损追踪)
        self.tracker.update_trackings()

    def _scan_intelligence(self, tick: Dict[str, Any]):
        """扫描订单流情报：隐形墙和空头挤压"""
        curr_ts = time.time()

        # 仅在持仓状态下扫描情报，避免不必要的计算
        if self.context.is_in_position and (curr_ts - self.last_intel_time > self.config.scan_interval):
            # 探测隐形墙
            wall_price = self.math_brain.detect_absorption_wall(tick)
            if wall_price:
                self.context.update_of_wall(wall_price, tick['ts'])

            # 探测空头挤压
            if self.math_brain.detect_short_squeeze(tick):
                self.context.update_of_squeeze(True, tick['ts'])

            self.last_intel_time = curr_ts

    async def _process_signal(self, signal_data: Dict[str, Any], tick: Dict[str, Any]):
        """处理订单流信号"""
        signal_level = signal_data.get('level', '')

        if signal_level == "STRICT":
            # 严格信号：需要SMC宏观验证 + 防阴跌陷阱
            await self._process_strict_signal(signal_data)

            # 科考船记录 (极快，留在主线程)
            self.tracker.add_tracking(signal_data)

        elif signal_level == "BROAD":
            # 宽口径信号：只记录，不执行
            logger.warning(
                f"🎯 捕获暗流(宽口径)！砸盘: ${abs(signal_data['cvd_delta_usdt']) / 10000:.1f}万。加入科考船..."
            )
            self.tracker.add_tracking(signal_data)

    async def _process_strict_signal(self, signal_data: Dict[str, Any]):
        """处理严格信号：异步验证 + 狙击执行"""
        # 将耗时的SMC验证放入后台线程，不阻塞Tick流
        asyncio.create_task(self._async_validate_and_execute(signal_data))

    async def _async_validate_and_execute(self, signal_data: Dict[str, Any]):
        """
        异步验证与执行：SMC宏观验证 + 防阴跌陷阱 + 实盘狙击

        注意：此函数在后台异步执行，不会阻塞主数据流
        """
        try:
            # 1. SMC宏观验证 (在后台线程中执行)
            # 🌟 新增：SMC 旁路机制 (支持纯高频剥头皮模式)
            if not self.config.smc_validation_enabled:
                is_safe = True
                # 伪造一个完美共振消息，骗过后面的防阴跌拦截机制
                smc_msg = "完美共振 [SMC已关闭，纯高频订单流模式]"
            else:
                # 原有的 SMC 宏观验证
                is_safe, smc_msg = await asyncio.to_thread(
                    self.smc_validator.final_check,
                    signal_data['local_low']
                )
            signal_data['smc_msg'] = smc_msg

            # 提取SMC验证结果标记
            is_perfect_terrain = "完美共振" in smc_msg
            effort_m = abs(signal_data.get('cvd_delta_usdt', 0)) / 1_000_000

            # 2. 防阴跌陷阱拦截 (仅保留核心逻辑)
            if is_safe:
                anti_slide_threshold_m = self.config.anti_slide_threshold / 1_000_000

                # 防连跌：如果地形一般，且空头砸盘量太小，说明恐慌没释放完
                if not is_perfect_terrain and effort_m < anti_slide_threshold_m:
                    logger.info(
                        f"🛡️ [防阴跌拦截] 普通支撑区且砸盘量太小({effort_m:.1f}M < {anti_slide_threshold_m:.1f}M)，未形成恐慌衰竭，拒绝接刀！"
                    )
                    signal_data['level'] = "REJECTED"
                    self.tracker.add_tracking(signal_data)
                    return

                # ======= 【实盘开火区】 =======
                logger.warning(f"🚨 [绝杀核弹] 微观订单流 + SMC宏观共振！")

                if self.mode == "live":
                    await self._execute_live_trade(signal_data)
                else:
                    # collect模式：只记录信号，不发送邮件
                    logger.info(f"📝 [收集模式] 记录信号但不发送邮件: 价格={signal_data['price']}, 砸盘=${abs(signal_data['cvd_delta_usdt']):,.0f} USDT")

                # 记录成功信号
                self.tracker.add_tracking(signal_data)

            else:
                # SMC验证失败，记录拦截信号
                signal_data['level'] = "REJECTED"
                self.tracker.add_tracking(signal_data)
                logger.info(f"🛡️ [影子拦截] 已将拦截信号存档供复盘: {smc_msg}")

        except Exception as e:
            logger.error(f"❌ 异步验证执行异常: {e}")
            # 即使异常也记录信号，便于调试
            signal_data['level'] = "ERROR"
            self.tracker.add_tracking(signal_data)

    async def _execute_live_trade(self, signal_data: Dict[str, Any]):
        """执行实盘交易"""
        logger.warning("🔫 [实盘模式] 正在向 OKX 发送真实买入指令！")

        # 获取SMC阻力位作为TP2目标
        tp2_target = await asyncio.to_thread(
            self.smc_validator.get_nearest_resistance,
            signal_data['price']
        )

        entry_price = signal_data['price']

        if tp2_target:
            # 计算阻力位涨幅
            resistance_pct = (tp2_target - entry_price) / entry_price
            logger.info(f"🎯 [TP2决策] SMC找到阻力位: {tp2_target:.2f} (涨幅: {resistance_pct * 100:.2f}%)")

            # 风控检查：阻力位是否太近
            min_tp2_price = entry_price * (1 + self.config.tp1_pct * 2)  # 0.8%
            if tp2_target < min_tp2_price:
                logger.warning(f"🛡️ [TP2决策] 阻力位太近({tp2_target:.2f} < {min_tp2_price:.2f})，使用配置比例")
                tp2_target = entry_price * (1 + self.config.tp2_pct)
        else:
            logger.warning("🎯 [TP2决策] SMC未找到阻力位，使用配置比例")
            tp2_target = entry_price * (1 + self.config.tp2_pct)

        logger.info(
            f"🎯 [TP2最终] 入场价: {entry_price:.2f}, TP2目标: {tp2_target:.2f} (涨幅: {(tp2_target / entry_price - 1) * 100:.2f}%)")

        # 执行交易并获取结果
        execution_result = await self.executor.execute_snipe(
            price=signal_data['price'],
            local_low=signal_data['local_low'],
            tp2_price=tp2_target
        )

        # 如果执行成功，启动生命周期管理
        if execution_result:
            await self.lifecycle_manager.start_lifecycle(execution_result)

    async def _send_email_alert(self, signal_data: Dict[str, Any]):
        """发送邮件警报 (收集模式)"""
        current_ts = time.time()
        if current_ts - self._last_email_sent_time < self._email_cooldown:
            return

        details = f"""
🚨 检测到机构恐慌吸收与绝地反击！
💰 开火现价: {signal_data['price']}
🕳️ 探明底价: {signal_data['local_low']}
📉 CVD砸盘: ${abs(signal_data['cvd_delta_usdt']):,.0f} USDT
📈 主力反抽: ${signal_data['micro_cvd']:,.0f} USDT
🚀 坑底反弹: {signal_data['price_diff_pct']:.3f}%
"""
        success = await send_trading_signal_email(
            self.symbol,
            "流速级抄底绝杀 (SMC装甲版)",
            signal_data['price'],
            details
        )

        if success:
            self._last_email_sent_time = current_ts

    async def run(self):
        """启动编排器主循环"""
        logger.info("🚀 启动订单流引擎编排器...")
        self._is_running = True

        # 1. 启动 SMC 验证器后台静默扫描
        asyncio.create_task(self.smc_validator.background_update_loop())

        # 2. 如果是实盘模式，启动后台闲时查账功能
        if self.mode == "live":
            asyncio.create_task(self.trader.update_balance_loop())

        # 3. 启动极速数据流连接
        await self.streamer.connect()

        logger.info("✅ 订单流引擎编排器已全面上线！")

    async def shutdown(self):
        """安全关闭编排器"""
        logger.info("🔔 正在安全关闭编排器...")
        self._is_running = False

        # 停止生命周期管理
        await self.lifecycle_manager.stop_lifecycle()

        # 强制关闭所有科考船记录
        self.tracker.force_close_all()

        logger.info("✅ 编排器已安全关闭")


def main():
    """主函数：命令行入口"""
    parser = argparse.ArgumentParser(description="Momentum 1.66 - 订单流引擎编排器")
    parser.add_argument('--symbol', type=str, default='ETH-USDT-SWAP',
                        help='交易对符号，例如: ETH-USDT-SWAP, BTC-USDT-SWAP')
    parser.add_argument('--mode', type=str, default='collect',
                        choices=['collect', 'live'],
                        help="运行模式: 'collect' (只收集数据和发邮件) 或 'live' (实盘自动交易)")
    args = parser.parse_args()

    # 创建编排器实例
    orchestrator = OrderFlowOrchestrator(symbol=args.symbol, mode=args.mode)
    logger.info(f"⚙️ 当前引擎运行模式: 【{args.mode.upper()}】")

    # 🌟 优雅重启，监听 kill -15
    def handle_sigterm(*args):
        logger.warning("🔔 收到 kill -15 信号！转换为安全迫降指令...")
        raise KeyboardInterrupt()  # 直接抛出异常，交给下面的 try-except 统一处理

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.warning("🔔 收到停止指令！准备安全迫降...")
        orchestrator.tracker.force_close_all()


if __name__ == "__main__":
    main()
