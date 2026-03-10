#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Triple-A引擎编排器 (Orchestrator)
基于单向数据流架构，协调所有Triple-A组件。

设计原则：
1. 单向数据流：Tick数据 → MarketContext → TripleADetector → 信号验证 → 执行 → 生命周期
2. 状态集中：MarketContext 作为唯一状态源
3. 组件解耦：各组件通过上下文和事件通信，而非直接依赖
4. 配置驱动：所有参数从 TripleAConfig 读取

数据流：
1. Tick数据 → MarketContext.update_tick() → TripleADetector.process_tick()
2. Triple-A信号 → MarketContext.update_signal() → 信号验证
3. 验证通过 → TripleAExecutor.execute_triple_a() → AdaptiveLifecycleManager.start_lifecycle()
4. 生命周期 → MarketContext.update_position() → 持续监控
"""
import argparse
import asyncio
import os
import signal
import sys
import time
import traceback
from typing import Dict, Any

# 确保能导入项目根目录的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_stream import OKXTickStreamer
from src.strategy.triple_a.detector import TripleADetector
from src.strategy.triple_a.tracker import TripleACSVTracker
from src.execution.triple_a_executor import TripleAExecutor
from src.execution.trader import OKXTrader
from src.context.market_context import MarketContext
from src.utils.log import get_logger
from src.utils.email_sender import send_trading_signal_email
from config.loader import load_triple_a_config

logger = get_logger(__name__)


class TripleAOrchestrator:
    """Triple-A引擎编排器"""

    def __init__(self, symbol: str = "ETH-USDT-SWAP", mode: str = "collect", research_mode: bool = False, config=None):
        """
        初始化编排器

        Args:
            symbol: 交易对符号
            mode: 运行模式 ('collect' 或 'live')
            research_mode: 科考船研究模式，更宽松的验证和风险控制
            config: 可选的配置对象或字典，如果提供则使用此配置
        """
        self.symbol = symbol
        self.mode = mode
        self.research_mode = research_mode

        # 加载配置
        if config is not None:
            # 如果提供了配置，使用它
            if isinstance(config, dict):
                # 如果是字典，转换为TripleAConfig对象
                from src.strategy.triple_a.config import TripleAConfig
                self.config = TripleAConfig.from_dict(config)
            else:
                # 假设已经是TripleAConfig对象
                self.config = config
            logger.info(f"✅ 使用提供的Triple-A配置")
        else:
            # 否则从文件加载
            try:
                self.config = load_triple_a_config(symbol)
                logger.info(f"✅ 成功加载 {symbol} Triple-A配置")
            except Exception as e:
                logger.error(f"❌ 加载配置失败: {e}, 使用默认配置")
                from src.strategy.triple_a.config import TripleAConfig
                self.config = TripleAConfig()

        # 创建线程安全的市场上下文 (核心状态存储)
        self.context = MarketContext()

        # ==================== 初始化核心组件 ====================

        # 1. Triple-A检测器 (核心策略逻辑)
        self.detector = TripleADetector(config=self.config, context=self.context)

        # 2. 科考船追踪器 (信号记录)
        self.tracker = self._create_tracker()

        # 3. 实盘交易器 (纯API层，仅live模式需要)
        self.trader = None
        if mode == "live":
            self.trader = OKXTrader(
                symbol=symbol,
                leverage=self.config.leverage,
                risk_pct=self.config.risk_pct,
                sl_pct=self.config.initial_sl_pct,
                context=self.context
            )

        # 4. Triple-A执行器 (交易执行逻辑)
        self.executor = TripleAExecutor(
            config=self.config,
            context=self.context,
            trader=self.trader,
            tracker=self.tracker,
            research_mode=research_mode
        )

        # 5. 数据流连接器 (Tick数据源)
        self.streamer = OKXTickStreamer(
            symbol=symbol,
            on_tick_callback=self.on_tick
        )

        # ==================== 运行时状态 ====================

        self._last_email_sent_time = 0
        self._email_cooldown = 60  # 邮件冷却时间60秒
        self._is_running = False

        # 信号统计
        self.signal_stats = {
            "absorption": 0,
            "accumulation": 0,
            "aggression": 0,
            "failed_auction": 0,
            "total": 0
        }

        logger.info(f"🚀 Triple-A编排器初始化完成: {symbol} [{mode.upper()}]")

    def _create_tracker(self):
        """创建Triple-A科考船追踪器"""
        return TripleACSVTracker(config=self.config, context=self.context)

    async def on_tick(self, tick: Dict[str, Any]):
        """
        核心数据流处理函数：接收Tick数据，驱动单向流水线

        数据流：Tick → Context → TripleADetector → 信号处理
        """
        # 0. 更新MarketContext中的最新Tick数据
        self.context.update_tick(tick)

        # 1. Triple-A检测器处理 (生成信号)
        signal_data = await self.detector.process_tick(tick)

        # 2. 信号分发处理
        if signal_data:
            await self._process_signal(signal_data, tick)

        # 3. 科考船更新
        self.tracker.update_trackings()

    async def _process_signal(self, signal_data: Dict[str, Any], tick: Dict[str, Any]):
        """处理Triple-A信号"""
        signal_type = signal_data.get('type', '')
        self.signal_stats["total"] += 1

        # 更新统计
        if 'ABSORPTION' in signal_type:
            self.signal_stats["absorption"] += 1
        elif 'ACCUMULATION' in signal_type:
            self.signal_stats["accumulation"] += 1
        elif 'AGGRESSION' in signal_type:
            self.signal_stats["aggression"] += 1
        elif 'FAILED_AUCTION' in signal_type:
            self.signal_stats["failed_auction"] += 1

        # 记录信号（科考船模式：只记录侵略和失败拍卖信号，减少数据量）
        if signal_type in ["AGGRESSION_TRIGGERED", "FAILED_AUCTION_DETECTED"]:
            self.tracker.add_tracking(signal_data)
        else:
            # 吸收和累积信号只记录到日志，不记录到CSV
            logger.debug(f"📝 跳过CSV记录: {signal_type} @ {signal_data.get('price', 0):.2f}")

        # 根据信号类型处理
        # 注意：现在信号处理改由执行器负责

        # 如果是Aggression信号，交给执行器处理

        if signal_type == "AGGRESSION_TRIGGERED":

            try:

                # 将信号交给执行器处理

                trade_result = await self.executor.execute_triple_a(signal_data)

                if trade_result:

                    logger.info(f"✅ 执行器处理Aggression信号成功")

            except Exception as e:

                logger.error(f"❌ 执行器处理Aggression信号失败: {e}")

        elif signal_type == "FAILED_AUCTION_DETECTED":

            try:

                # 将信号交给执行器处理

                stop_result = await self.executor.execute_triple_a(signal_data)

                if stop_result:

                    logger.info(f"✅ 执行器处理Failed Auction信号成功")

            except Exception as e:

                logger.error(f"❌ 执行器处理Failed Auction信号失败: {e}")

        # 科考船模式：不发送信号邮件，只记录到CSV/日志
        # 邮件只用于系统级错误（代码崩溃、账号问题等）
        # if self.mode == "collect" and "FAILED_AUCTION" in signal_type:
        #     await self._send_email_alert(signal_data, tick)




    async def _send_email_alert(self, signal_data: Dict[str, Any], tick: Dict[str, Any]):
        """发送邮件警报（收集模式）- 科考船模式禁用邮件"""
        # 科考船模式不发送邮件，只记录日志
        signal_type = signal_data.get('type', 'UNKNOWN')
        price = signal_data.get('price', 0)
        score = signal_data.get('score', 0)

        logger.info(f"📊 信号记录（邮件禁用）: {signal_type} @ {price:.2f}, 得分: {score:.2f}")

        # 不发送邮件，直接返回
        return False

    async def run(self):
        """启动编排器主循环"""
        logger.info("🚀 启动Triple-A引擎编排器...")
        self._is_running = True

        # 启动极速数据流连接
        await self.streamer.connect()

        logger.info("✅ Triple-A引擎编排器已全面上线！")

    async def shutdown(self):
        """安全关闭编排器"""
        logger.info("🔔 正在安全关闭编排器...")
        self._is_running = False

        # 强制关闭所有科考船记录
        self.tracker.force_close_all()

        logger.info("✅ 编排器已安全关闭")

    def get_stats(self) -> Dict[str, Any]:
        """获取编排器统计信息"""
        return {
            "symbol": self.symbol,
            "mode": self.mode,
            "signals": self.signal_stats,
            "detector_stats": self.detector.get_stats() if hasattr(self.detector, 'get_stats') else {},
            "is_running": self._is_running
        }


def main():
    """主函数：命令行入口"""
    parser = argparse.ArgumentParser(description="Momentum 1.66 - Triple-A引擎编排器")
    parser.add_argument('--symbol', type=str, default='ETH-USDT-SWAP',
                        help='交易对符号，例如: ETH-USDT-SWAP, BTC-USDT-SWAP')
    parser.add_argument('--mode', type=str, default='collect',
                        choices=['collect', 'live'],
                        help="运行模式: 'collect' (只收集数据和发邮件) 或 'live' (实盘自动交易)")
    args = parser.parse_args()

    # 创建编排器实例
    orchestrator = TripleAOrchestrator(symbol=args.symbol, mode=args.mode)
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
    except Exception as e:
        logger.error(f"❌ Triple-A引擎发生严重错误，程序即将终止: {e}", exc_info=True)

        # 发送系统错误邮件
        try:
            error_details = f"""
🚨 Triple-A引擎系统崩溃！

📊 错误类型: {type(e).__name__}
❌ 错误信息: {str(e)}
📁 交易对: {args.symbol}
🔄 运行模式: {args.mode}
🕒 发生时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}

📋 堆栈跟踪:
{traceback.format_exc()}
"""
            # 尝试发送邮件，但邮件发送失败不影响错误处理
            asyncio.run(send_trading_signal_email(
                args.symbol,
                f"🚨 Triple-A引擎系统崩溃 ({type(e).__name__})",
                0.0,
                error_details
            ))
            logger.info("📧 系统错误邮件已发送")
        except Exception as mail_error:
            logger.error(f"❌ 发送系统错误邮件失败: {mail_error}")

        # 重新抛出异常，让程序终止
        raise


if __name__ == "__main__":
    main()