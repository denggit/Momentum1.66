#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26 8:53 PM
@File       : strategy.py
@Description: 
"""
import argparse
import asyncio
import os
import signal
import sys
import time

current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_stream import OKXTickStreamer
from src.strategy.orderflow import OrderFlowMath
from src.utils.log import get_logger
from src.utils.email_sender import send_trading_signal_email
from src.execution.trader import OKXTrader
from src.execution.orderflow_executor import OrderFlowExecutor
from src.execution.lifecycle_manager import LifecycleManager
from engines.engine_3_orderflow.tracker import CSVTracker
from src.strategy.smc_validator import SMCValidator
from src.context.market_context import MarketContext
from config.loader import load_orderflow_config

logger = get_logger(__name__)


class Engine3Commander:
    def __init__(self, symbol="ETH-USDT-SWAP", mode="collect"):
        self.symbol = symbol
        self.mode = mode

        # 加载订单流配置
        try:
            self.config = load_orderflow_config(symbol)  # 默认返回OrderFlowConfig对象
            logger.info(f"✅ 成功加载 {symbol} 订单流配置")
        except Exception as e:
            logger.error(f"❌ 加载配置失败: {e}, 使用默认配置")
            # 创建默认配置对象
            from src.strategy.orderflow_config import OrderFlowConfig
            self.config = OrderFlowConfig()

        # 创建线程安全的市场上下文
        self.context = MarketContext()

        # 创建订单流数学大脑（传入配置和上下文）
        self.math_brain = OrderFlowMath(config=self.config, context=self.context)

        self.tracker = CSVTracker(project_root)

        # 🌟 集成 SMC 验证模块
        self.smc_validator = SMCValidator(symbol=symbol, timeframes=self.config.smc_timeframes)

        # 🌟 实例化你的实盘枪手（纯API层）
        self.trader = OKXTrader(
            symbol=symbol,
            leverage=self.config.leverage,
            risk_pct=self.config.risk_pct,
            sl_pct=self.config.sl_pct,
            context=self.context  # 传递MarketContext
        )

        # 🌟 创建OrderFlow执行策略器（封装三连发逻辑）
        self.executor = OrderFlowExecutor(trader=self.trader, config=self.config)

        # 🌟 创建生命周期管理器
        self.lifecycle_manager = LifecycleManager(
            trader=self.trader,
            context=self.context,
            config=self.config
        )

        # 将 on_tick_callback 指向自己的处理函数
        self.streamer = OKXTickStreamer(symbol=symbol, on_tick_callback=self.on_tick)

        self._last_email_sent_time = 0
        # 从配置获取email_cooldown
        self._email_cooldown = self.config.email_cooldown

        self.last_intel_time = 0

    def on_tick(self, tick: dict):
        """核心枢纽：接收数据 -> 算术 -> 分发信号 -> 更新追踪"""

        signal_data = self.math_brain.process_tick(tick)

        # =========================================================
        # 🌟 关键新增：情报实时同步给 MarketContext (性能洁癖版)
        # =========================================================
        curr_ts = time.time()
        # 每 scan_interval 秒扫描一次情报，直接同步执行数学运算，抛弃 create_task 的调度开销！
        if self.context.is_in_position and (curr_ts - self.last_intel_time > self.config.scan_interval):
            wall_price = self.math_brain.detect_absorption_wall(tick)
            if wall_price:
                self.context.update_of_wall(wall_price, tick['ts'])

            if self.math_brain.detect_short_squeeze(tick):
                self.context.update_of_squeeze(True, tick['ts'])

            self.last_intel_time = curr_ts
        # =========================================================

        if signal_data:
            if signal_data['level'] == "STRICT":
                # 🌟 优化：把耗时的宏观校验和下单，扔给异步后台去跑，绝不卡顿 Tick 流！
                asyncio.create_task(self._async_evaluate_and_snipe(signal_data))

                # 科考船记录（极快，留在主线程）
                self.tracker.add_tracking(signal_data)

            elif signal_data['level'] == "BROAD":
                logger.warning(
                    f"🎯 捕获暗流(宽口径)！砸盘: ${abs(signal_data['cvd_delta_usdt']) / 10000:.1f}万。加入科考船...")
                self.tracker.add_tracking(signal_data)

        # 3. 让科考船更新最高价和止损
        self.tracker.update_trackings(tick['price'], tick['ts'])

    # 🌟 新增的异步验证与狙击函数
    async def _async_evaluate_and_snipe(self, signal_data):
        is_safe, smc_msg = await asyncio.to_thread(self.smc_validator.final_check, signal_data['local_low'])
        signal_data['smc_msg'] = smc_msg

        is_perfect_terrain = "完美共振" in smc_msg
        effort_m = abs(signal_data.get('cvd_delta_usdt', 0)) / 1_000_000

        # ==========================================
        # 🌟 核心拦截逻辑：仅保留防阴跌陷阱 (撤销了2000万上限)
        # ==========================================
        if is_safe:
            # 防连跌：如果地形一般，且空头砸盘量太小 (< anti_slide_threshold)，说明恐慌根本没释放完
            # 极大概率是阴跌中继，拒绝接刀！
            anti_slide_threshold_m = self.config.anti_slide_threshold / 1_000_000
            if not is_perfect_terrain and effort_m < anti_slide_threshold_m:
                logger.info(
                    f"🛡️ [防阴跌拦截] 普通支撑区且砸盘量太小({effort_m:.1f}M < {anti_slide_threshold_m:.1f}M)，未形成恐慌衰竭，拒绝接刀！")
                signal_data['level'] = "REJECTED"
                self.tracker.add_tracking(signal_data)
                return

                # ======= 【实盘开火区】 =======
            logger.warning(f"🚨 [绝杀核弹] 微观订单流 + SMC宏观共振！")

            if self.mode == "live":
                logger.warning("🔫 [实盘模式] 正在向 OKX 发送真实买入指令！")

                tp2_target = await asyncio.to_thread(self.smc_validator.get_nearest_resistance, signal_data['price'])
                if not tp2_target:
                    # 从配置获取tp2_pct
                    tp2_target = signal_data['price'] * (1 + self.config.tp2_pct)

                # 执行交易并获取结果（通过OrderFlow执行策略器）
                execution_result = await self.executor.execute_snipe(
                    price=signal_data['price'],
                    local_low=signal_data['local_low'],
                    tp2_price=tp2_target
                )

                # 如果执行成功，启动生命周期管理
                if execution_result:
                    await self.lifecycle_manager.start_lifecycle(execution_result)
            else:
                await self.send_email_alert(signal_data)
            self.tracker.add_tracking(signal_data)

        else:
            # ======= 【影子科考区】 =======
            signal_data['level'] = "REJECTED"
            self.tracker.add_tracking(signal_data)
            logger.info(f"🛡️ [影子拦截] 已将拦截信号存档供复盘: {smc_msg}")

    async def send_email_alert(self, signal):
        current_ts = time.time()
        if current_ts - self._last_email_sent_time < self._email_cooldown:
            return

        details = f"""
🚨 检测到机构恐慌吸收与绝地反击！
💰 开火现价: {signal['price']}
🕳️ 探明底价: {signal['local_low']}
📉 CVD砸盘: ${abs(signal['cvd_delta_usdt']):,.0f} USDT
📈 主力反抽: ${signal['micro_cvd']:,.0f} USDT
🚀 坑底反弹: {signal['price_diff_pct']:.3f}%
"""
        success = await send_trading_signal_email(self.symbol, "流速级抄底绝杀 (SMC装甲版)", signal['price'],
                                                  details)
        if success:
            self._last_email_sent_time = current_ts

    async def run(self):
        logger.info("🚀 启动 Engine 3 订单流总指挥部...")

        # 🌟 1. 启动 SMC 验证器后台静默扫描 (严格对齐 00 秒)
        asyncio.create_task(self.smc_validator.background_update_loop())

        # 🌟 2. 如果是实盘模式，启动后台闲时查账功能
        if self.mode == "live":
            asyncio.create_task(self.trader.update_balance_loop())

        # 3. 启动极速数据流连接
        await self.streamer.connect()


if __name__ == "__main__":
    # 🌟 增加命令行参数解析
    parser = argparse.ArgumentParser(description="Momentum 1.66 - 订单流三号引擎")
    parser.add_argument('--mode', type=str, default='collect', choices=['collect', 'live'],
                        help="运行模式: 'collect' (只收集数据和发邮件) 或 'live' (实盘自动交易)")
    args = parser.parse_args()

    # 将解析到的模式传给指挥部
    commander = Engine3Commander(mode=args.mode)
    logger.info(f"⚙️ 当前引擎运行模式: 【{args.mode.upper()}】")

    # 🌟 优雅重启，监听 kill -15
    def handle_sigterm(*args):
        logger.warning("🔔 收到 kill -15 信号！转换为安全迫降指令...")
        raise KeyboardInterrupt()  # 直接抛出异常，交给下面的 try-except 统一处理


    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        asyncio.run(commander.run())
    except KeyboardInterrupt:
        logger.warning("🔔 收到停止指令！准备安全迫降...")
        commander.tracker.force_close_all()
