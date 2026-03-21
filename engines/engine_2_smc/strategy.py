#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/2/26 11:26 PM
@File       : strategy.py
@Description: 二号引擎 SMC 实盘策略入口 (兼容旧版本)
              实际实现已移至 src.strategy.smc_validator
              此文件作为 SMC 实盘策略的启动入口
"""
import argparse
import asyncio
import os
import signal
import sys

# 确保能导入 src 目录下的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 导入新的 SMC 编排器
from engines.engine_2_smc.orchestrator import SMCOrchestrator
# 保持向后兼容，MicroSMCRadar 类仍然可用
from src.strategy.orderflow.smc_validator import MicroSMCRadar
from src.utils.log import get_logger

logger = get_logger(__name__)


def main():
    """SMC 二号引擎主入口"""
    parser = argparse.ArgumentParser(description="Momentum 1.66 - SMC 二号引擎 (实盘策略)")
    parser.add_argument('--symbol', type=str, default='ETH-USDT-SWAP',
                        help='交易对符号，例如: ETH-USDT-SWAP, BTC-USDT-SWAP')
    parser.add_argument('--mode', type=str, default='collect', choices=['collect', 'live'],
                        help="运行模式: 'collect' (只收集信号) 或 'live' (实盘自动交易)")
    parser.add_argument('--test-radar', action='store_true',
                        help='测试 SMC 雷达功能（旧版本兼容）')
    args = parser.parse_args()

    if args.test_radar:
        # 运行旧的雷达测试
        logger.info("🛰️ 启动 SMC 雷达测试...")
        radar = MicroSMCRadar(symbol=args.symbol, timeframes=["5m", "15m", "1H"])
        radar.update_structure()
        print("\n🗺️ 当前算出的 5m 支撑防线：")
        for p in radar.active_pois:
            print(f"[{p['type']}] 顶部: {p['top']}, 底部: {p['bottom']}, 生成时间: {p['time']}")

        test_price = 1980.2
        is_safe, msg = radar.is_in_poi(test_price)
        print(f"\n现价 {test_price} 能否抄底？ -> {is_safe} ({msg})")
        return

    # 运行新的 SMC 编排器
    orchestrator = SMCOrchestrator(symbol=args.symbol, mode=args.mode)
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
        asyncio.run(orchestrator.shutdown())


if __name__ == "__main__":
    main()
