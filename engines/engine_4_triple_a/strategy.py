#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/9/26
@File       : strategy.py
@Description: Triple-A四号引擎主入口 (基于单向数据流架构)
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

from src.engine.triple_a_orchestrator import TripleAOrchestrator
from src.utils.log import get_logger
from config.loader import load_triple_a_config
import os

logger = get_logger(__name__)

if __name__ == "__main__":
    # 🌟 增加命令行参数解析
    parser = argparse.ArgumentParser(description="Momentum 1.66 - Triple-A四号引擎 (单向数据流架构)")
    parser.add_argument('--symbol', type=str, default='ETH-USDT-SWAP',
                        help='交易对符号，例如: ETH-USDT-SWAP, BTC-USDT-SWAP')
    parser.add_argument('--mode', type=str, default='collect', choices=['collect', 'live'],
                        help="运行模式: 'collect' (只收集数据和发邮件) 或 'live' (实盘自动交易)")
    args = parser.parse_args()

    # 根据模式加载配置：收集模式使用研究配置，实盘模式使用默认配置
    if args.mode == "collect":
        research_symbol = f"{args.symbol}-RESEARCH"
        research_config_path = os.path.join("config", "triple_a", f"{research_symbol}.yaml")
        if os.path.exists(research_config_path):
            logger.info(f"📊 收集模式：使用研究配置文件 {research_symbol}.yaml")
            config = load_triple_a_config(research_symbol, return_dict=False)
        else:
            logger.info(f"📊 收集模式：未找到研究配置，使用默认配置")
            config = load_triple_a_config(args.symbol, return_dict=False)
    else:
        logger.info(f"🚀 实盘模式：使用默认配置")
        config = load_triple_a_config(args.symbol, return_dict=False)

    # 创建编排器实例
    orchestrator = TripleAOrchestrator(symbol=args.symbol, mode=args.mode, config=config)
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
            if hasattr(orchestrator, 'tracker'):
                orchestrator.tracker.force_close_all()