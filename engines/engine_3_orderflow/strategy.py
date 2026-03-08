#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26 8:53 PM
@File       : strategy.py
@Description: 订单流三号引擎主入口 (使用新版Orchestrator)
"""
import argparse
import asyncio
import os
import signal
import sys

current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.engine.orchestrator import OrderFlowOrchestrator
from src.utils.log import get_logger

logger = get_logger(__name__)

if __name__ == "__main__":
    # 🌟 增加命令行参数解析
    parser = argparse.ArgumentParser(description="Momentum 1.66 - 订单流三号引擎 (Orchestrator版)")
    parser.add_argument('--symbol', type=str, default='ETH-USDT-SWAP',
                        help='交易对符号，例如: ETH-USDT-SWAP, BTC-USDT-SWAP')
    parser.add_argument('--mode', type=str, default='collect', choices=['collect', 'live'],
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
        # 尝试异步关闭编排器
        try:
            import asyncio as aio

            loop = aio.new_event_loop()
            aio.set_event_loop(loop)
            loop.run_until_complete(orchestrator.shutdown())
            loop.close()
        except Exception as e:
            logger.error(f"❌ 关闭编排器时出错: {e}")
            # 至少强制关闭科考船记录
            orchestrator.tracker.force_close_all()
