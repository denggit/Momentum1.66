#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TripleA 五号引擎编排器 (TripleA Orchestrator v5)
精简版单向数据流架构，仅包含Tick数据接入和交易执行接口。

核心原则：
1. 单向数据流：Tick数据 → 处理管道 → 交易执行
2. 模块化设计：每个功能后续独立填充
3. 接口兼容：保持与四号引擎相同的核心接口
4. 渐进开发：禁止实现未要求的功能

当前版本仅实现：
- Tick数据WebSocket接入
- 交易执行器接口占位
- 基础运行框架

后续填充顺序：
1. Range Bar生成器
2. CVD计算引擎
3. KDE密度估计
4. LVN区域管理
5. 状态机（5状态模型）
6. 风控系统
7. 信号生成器
8. 影子引擎
"""
import argparse
import asyncio
import json
import os
import signal
import sys

import aiohttp

# 确保能导入项目根目录的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.execution.trader import OKXTrader
from engines.engine_5_triplea_new.execution_manager import TripleAExecutionManager
from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleAOrchestrator:
    """五号引擎编排器（精简版）"""

    def __init__(self, symbol: str = "ETH-USDT-SWAP", mode: str = "collect"):
        """
        初始化编排器

        Args:
            symbol: 交易对，例如 ETH-USDT-SWAP
            mode: 运行模式，'collect'（收集模式）或 'live'（实盘模式）
        """
        self.symbol = symbol
        self.mode = mode

        # ==========================================
        # 1. 核心组件实例化（仅包含用户要求的部分）
        # ==========================================

        # 🏦 交易执行器（用户明确要求）
        self.trader = OKXTrader(symbol=symbol, leverage=20, risk_pct=0.5)

        # 🛠️ 执行管理器（用户要求包含调用trader下单的部分）
        self.execution_manager = TripleAExecutionManager(trader=self.trader)

        # 🔌 信号生成器占位（后续填充）
        self.signal_generator = None  # 后续替换为实际的信号生成器

        # 📊 当前价格
        self.current_price = 0.0

        # 🏃 运行状态控制
        self._is_running = False
        self._tasks = []

        logger.info(f"🚀 TripleA 五号引擎编排器初始化完成: {symbol} [{mode.upper()}]")
        logger.info("📋 当前版本：仅包含Tick数据接入和交易执行接口")
        logger.info("🔄 后续功能将按阶段逐步填充")

    async def run(self):
        """启动编排器主循环"""
        logger.info("🚀 启动 TripleA 五号引擎司令部...")
        self._is_running = True

        # ==========================================
        # 启动用户要求的核心功能
        # ==========================================

        # 1. 余额同步（仅实盘模式，用户要求包含trader下单相关）
        if self.mode == "live":
            self._tasks.append(asyncio.create_task(self.trader.update_balance_loop()))
            logger.info("💰 余额同步循环已启动（实盘模式）")

        # 2. Tick数据流（用户明确要求）
        self._tasks.append(asyncio.create_task(self._ws_tick_loop()))
        logger.info("📡 Tick数据流协程已启动")

        # 3. 信号处理器占位（后续填充）
        # 暂不启动，等待信号生成器实现

        logger.info("✅ 司令部基础框架已上线，等待功能填充...")

        # 保持主线程存活
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        """安全关闭编排器"""
        logger.warning("🔔 正在安全关闭 TripleA 五号引擎编排器...")
        self._is_running = False

        for task in self._tasks:
            if not task.done():
                task.cancel()

        logger.info("✅ TripleA 五号引擎编排器已安全迫降。")

    async def _ws_tick_loop(self):
        """
        Tick数据流协程：直连OKX WebSocket接收Tick数据

        注意：当前版本仅接收和打印Tick，不进行任何处理
        后续将添加数据管道，将Tick传递给RangeBar生成器等组件
        """
        ws_url = "wss://ws.okx.com:8443/ws/v5/public"
        subscribe_payload = {
            "op": "subscribe",
            "args": [{"channel": "trades", "instId": self.symbol}]
        }

        tick_counter = 0
        while self._is_running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, timeout=10) as ws:
                        logger.info("🔌 [WebSocket] 已连接到 OKX Tick 极速数据流！")
                        await ws.send_json(subscribe_payload)

                        async for msg in ws:
                            if not self._is_running:
                                break

                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)

                                # 解析Trades频道数据
                                if "data" in data and isinstance(data["data"], list):
                                    for trade in data["data"]:
                                        # 转换成标准Tick格式
                                        tick = {
                                            'price': float(trade['px']),
                                            'size': float(trade['sz']),
                                            'side': trade['side'],
                                            'ts': int(trade['ts'])
                                        }

                                        # 更新当前价格
                                        self.current_price = tick['price']

                                        # 🔄 单向数据流：将Tick传递给处理管道
                                        # 当前版本仅打印日志，后续添加实际处理
                                        tick_counter += 1
                                        if tick_counter % 100 == 0:
                                            logger.info(
                                                f"📊 已接收 {tick_counter} 个Tick | "
                                                f"最新价格: {tick['price']:.2f} | "
                                                f"模式: {self.mode.upper()}"
                                            )

                                        # TODO: 后续将此处替换为实际的数据管道调用
                                        # await self._process_tick_pipeline(tick)

                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning("⚠️ WebSocket连接断开，准备重连...")
                                break

            except Exception as e:
                logger.error(f"❌ WebSocket异常 ({e})，2秒后重连...")
                await asyncio.sleep(2)

    async def _handle_signal(self, signal: dict):
        """
        处理信号（占位方法）

        当前版本仅打印日志，不实际执行交易
        后续将调用execution_manager执行交易

        Args:
            signal: 信号字典，包含action, entry_price等信息
        """
        action = signal.get('action', 'UNKNOWN')
        entry_price = signal.get('entry_price', 0.0)
        reason = signal.get('reason', 'UNKNOWN')

        logger.info(
            f"📡 信号处理接口被调用 | "
            f"动作: {action} | "
            f"入场价: {entry_price:.2f} | "
            f"原因: {reason}"
        )

        # TODO: 后续根据信号类型调用execution_manager
        # if reason == "TRIPLE_A_COMPLETE":
        #     await self.execution_manager.execute_signal(signal)


def main():
    """主函数：命令行入口"""
    parser = argparse.ArgumentParser(
        description="Momentum 1.66 - TripleA 五号引擎编排器（精简版）"
    )
    parser.add_argument(
        '--symbol',
        type=str,
        default='ETH-USDT-SWAP',
        help='交易对，例如: ETH-USDT-SWAP'
    )
    parser.add_argument(
        '--mode',
        type=str,
        default='collect',
        choices=['collect', 'live'],
        help="运行模式: 'collect'（收集模式）或 'live'（实盘模式）"
    )
    args = parser.parse_args()

    orchestrator = TripleAOrchestrator(symbol=args.symbol, mode=args.mode)

    def handle_sigterm(*args):
        logger.warning("🔔 收到系统中断信号！安全迫降中...")
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.warning("🔔 用户手动停止！准备安全退出...")
        asyncio.run(orchestrator.shutdown())


if __name__ == "__main__":
    main()