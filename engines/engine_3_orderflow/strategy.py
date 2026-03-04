#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26 8:53 PM
@File       : strategy.py
@Description: 
"""
import asyncio
import os
import signal
import sys
import time
import argparse

current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_stream import OKXTickStreamer
from src.strategy.orderflow import OrderFlowMath
from engines.engine_3_orderflow.tracker import CSVTracker
from src.utils.log import get_logger
from src.utils.email_sender import send_trading_signal_email

logger = get_logger(__name__)


class Engine3Commander:
    def __init__(self, symbol="ETH-USDT-SWAP", mode="collect"):
        self.symbol = symbol
        self.mode = mode  # 记录当前运行模式
        self.math_brain = OrderFlowMath()
        self.tracker = CSVTracker(project_root)

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
                logger.warning("\n" + "🟢" * 25)
                logger.warning(f"🚨 [流速级抄底绝杀] 发现深海冰山！散户正在被集中血洗！")

                # 🌟 核心开关：只有在 live 模式下，才去执行实盘下单！
                if self.mode == "live":
                    logger.warning("🔫 [实盘模式] 正在向 OKX 发送真实买入指令！")
                    # ========================================================
                    # if Engine2.is_in_poi(tick['price']):
                    #     self.execute_real_trade(...)
                    # ========================================================
                else:
                    logger.warning("🛡️ [科考模式] 满足绝杀条件，但当前为收集模式，不执行真实下单。")

                asyncio.create_task(self.send_email_alert(signal_data))
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
🚨 检测到机构恐慌吸收信号！
💰 触发价格: {signal['price']}
📉 CVD砸盘: ${abs(signal['cvd_delta_usdt']):,.0f} USDT
📈 主力反抽: ${signal['micro_cvd']:,.0f} USDT
"""
        success = await send_trading_signal_email(self.symbol, "流速级抄底绝杀", signal['price'], details)
        if success:
            self._last_email_sent_time = current_ts

    async def run(self):
        logger.info("🚀 启动 Engine 3 订单流总指挥部...")
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
