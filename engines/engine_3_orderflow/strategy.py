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
from engines.engine_3_orderflow.tracker import CSVTracker
from engines.engine_2_smc.strategy import MicroSMCRadar

logger = get_logger(__name__)


class Engine3Commander:
    def __init__(self, symbol="ETH-USDT-SWAP", mode="collect"):
        self.symbol = symbol
        self.mode = mode
        self.math_brain = OrderFlowMath()
        self.tracker = CSVTracker(project_root)

        # 🌟 雇佣二号引擎雷达兵！
        self.smc_radar = MicroSMCRadar(symbol=symbol, timeframe="5m")

        # 🌟 实例化你的实盘枪手 (默认 20倍杠杆)
        self.trader = OKXTrader(symbol=symbol, leverage=50, risk_pct=0.5) # 每次用 50% 的仓位

        # 将 on_tick_callback 指向自己的处理函数
        self.streamer = OKXTickStreamer(symbol=symbol, on_tick_callback=self.on_tick)

        self._last_email_sent_time = 0
        self._email_cooldown = 600

    def on_tick(self, tick: dict):
        """核心枢纽：接收数据 -> 算术 -> 分发信号 -> 更新追踪"""

        # 1. 交给数学大脑计算
        signal_data = self.math_brain.process_tick(tick)

        # 2. 如果有信号，判断级别并执行动作
        if signal_data:
            if signal_data['level'] == "STRICT":
                # ========================================================
                # 🌟 宏观结构大审查！拿刚刚探明的“坑底价 local_low”去问二号引擎
                is_safe, smc_msg = self.smc_radar.is_in_poi(signal_data['local_low'])
                # ========================================================

                if is_safe:
                    logger.warning("\n" + "🟢" * 25)
                    logger.warning(f"🚨 [绝杀核弹] 微观订单流 + SMC宏观共振！")
                    logger.warning(f"🗺️ 宏观支持: {smc_msg} (完美命中坑底 {signal_data['local_low']})")
                    logger.warning(
                        f"💥 微观盘口: 砸盘 ${abs(signal_data['cvd_delta_usdt']) / 10000:.1f}万，反转 ${signal_data['micro_cvd'] / 10000:.1f}万，反弹了 {signal_data['price_diff_pct']:.3f}%")

                    if self.mode == "live":
                        logger.warning("🔫 [实盘模式] 正在向 OKX 发送真实买入指令！")
                        # 🌟 极其优雅的非阻塞实盘开火！
                        # risk_usdt 填入你愿意每次动用的实盘本金（比如 200U）
                        asyncio.create_task(self.trader.execute_snipe(
                            price=signal_data['price'],
                            local_low=signal_data['local_low'],
                        ))
                    else:
                        # 🌟 只有在 collect (科考) 模式下，才发送邮件报警
                        asyncio.create_task(self.send_email_alert(signal_data))
                else:
                    logger.info(
                        f"🛡️ [防撞墙启动] 发现极速反转，但坑底价 {signal_data['local_low']} {smc_msg}。拒绝接刀！")

                self.tracker.add_tracking(signal_data)

            elif signal_data['level'] == "BROAD":
                logger.warning(
                    f"🎯 捕获暗流(宽口径)！砸盘: ${abs(signal_data['cvd_delta_usdt']) / 10000:.1f}万。加入科考船...")
                self.tracker.add_tracking(signal_data)

        # 3. 让科考船更新最高价和止损
        self.tracker.update_trackings(tick['price'], tick['ts'])

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

        # 🌟 1. 启动 SMC 雷达后台静默扫描 (严格对齐 00 秒)
        asyncio.create_task(self.smc_radar.background_update_loop())

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
