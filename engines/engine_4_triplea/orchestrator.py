#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TripleA 四号引擎编排器 (TripleA Orchestrator)
将订单流微观信号转化为实盘高频策略。

核心架构 (多线程/协程单向数据流)：
1. 财务协程：定期更新可用余额。
2. 地图协程：每 5 分钟拉取一次 1m K线，重绘 Volume Profile 宏观地图。
3. 毫秒 Tick 协程：直连 OKX WebSocket，驱动微观引擎全速运转。
4. 状态同步：本地微观引擎的飞行模式 (LONG/SHORT) 完美镜像交易所的挂单状态。
"""
import argparse
import asyncio
import csv
import json
import os
import signal
import sys
import copy
from datetime import datetime

import aiohttp

# 确保能导入项目根目录的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_loader import OKXDataLoader
from src.utils.volume_profile_builder import VolumeProfileBuilder
from src.strategy.triplea.signal_generator import TripleASignalGenerator
from src.strategy.triplea.research_generator import ResearchTripleASignalGenerator
from src.execution.trader import OKXTrader
from engines.engine_4_triplea.execution_manager import TripleAExecutionManager
from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleAOrchestrator:
    def __init__(self, symbol: str = "ETH-USDT-SWAP", mode: str = "collect"):
        self.symbol = symbol
        self.mode = mode

        # ==========================================
        # 1. 实例化核心组件
        # ==========================================
        self.trader = OKXTrader(symbol=symbol, leverage=20, risk_pct=0.5)
        self.vp_builder = VolumeProfileBuilder(value_area_pct=0.70, bin_size=0.5, zone_pct=0.002)
        self.data_loader = OKXDataLoader(symbol=symbol, timeframe="1m")
        self.execution_manager = TripleAExecutionManager(trader=self.trader)

        # ⚔️ 主炮塔：实盘执行引擎 (参数极其严苛)
        self.main_generator = TripleASignalGenerator(symbol=symbol)

        # 👻 影子引擎：科考打捞船 (参数故意放宽，用于测试边界)
        self.shadow_queue = asyncio.Queue(maxsize=10000)  # 影子引擎专用队列
        self.shadow_generator = ResearchTripleASignalGenerator(symbol=symbol)
        self.shadow_generator.vol_spike_threshold = 1.5  # 放宽爆量倍数 (主炮塔是 2.0)
        self.shadow_generator.delta_ratio_threshold = 0.25  # 放宽净买卖比 (主炮塔是 0.35)

        # 📝 影子引擎的运行状态缓存与日志
        self.shadow_active_trade = {}
        self.log_file = f"data/tripleA/shadow_research_{symbol}.csv"
        self._init_research_vessel()

        self._is_running = False
        self._tasks = []

        logger.info(f"🚀 TripleA 四号引擎编排器初始化完成: {symbol} [{mode.upper()}]")

    def _init_research_vessel(self):
        """初始化科考船 CSV 表头（包含完整数据）"""
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        with open(self.log_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                # 基础信息
                "Entry_Time", "Close_Time", "Action", "Entry_Price",
                "Close_Price", "SL_Price", "TP_Price", "Score", "Close_Reason", "Gross_PnL",
                # 🆕 增加终极评价指标
                "MFE_Distance", "MAE_Distance",
                # 时间戳信息
                "A1_Start_Time", "A1_End_Time", "A2_Start_Time", "A2_End_Time",
                "A3_Start_Time", "A3_End_Time", "Entry_Time_Unix",
                # CVD指标
                "Global_CVD", "Global_Volume", "Delta_Ratio", "Recent_Vol", "Recent_CVD", "Recent_Delta_Ratio",
                # 诊断数据
                "Box_Size", "Vol_Spike_Threshold", "Delta_Ratio_Threshold",
                # 交易区域信息
                "Tradable_Zones_JSON"
            ])

    async def run(self):
        """启动编排器主循环"""
        logger.info("🚀 启动 TripleA 高频引擎司令部...")
        self._is_running = True

        # 启动财务官任务 (仅实盘模式)
        if self.mode == "live":
            self._tasks.append(asyncio.create_task(self.trader.update_balance_loop()))

        # 启动宏观地图刷新任务
        self._tasks.append(asyncio.create_task(self._macro_map_loop()))

        # 稍微等 3 秒，让第一张地图画好，再启动雷达
        await asyncio.sleep(3)

        # 启动毫秒级 Tick 数据流和微观引擎
        self._tasks.append(asyncio.create_task(self._ws_tick_loop()))

        # 启动影子引擎异步消费者
        self._tasks.append(asyncio.create_task(self._shadow_engine_consumer()))

        logger.info("✅ 司令部已全面上线，所有雷达全速运转中！")

        # 保持主线程存活
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        """安全关闭编排器"""
        logger.warning("🔔 正在安全关闭 TripleA 编排器...")
        self._is_running = False

        for task in self._tasks:
            if not task.done():
                task.cancel()

        logger.info("✅ TripleA 编排器已安全迫降。")

    async def _macro_map_loop(self):
        """宏观地图更新协程：每 5 分钟重绘一次战区地图"""
        while self._is_running:
            try:
                logger.debug("🗺️ 参谋部：正在拉取过去 24 小时数据，重绘宏观地图...")
                # 获取 24小时 的 1分钟K线 (1440 根)
                df = await asyncio.to_thread(self.data_loader.fetch_historical_data, limit=1440)

                if not df.empty:
                    # 构建 Volume Profile
                    profile_data = self.vp_builder.build_profile(df)

                    if profile_data:
                        # 🗺️ 将新地图同时喂给主炮塔和影子雷达
                        self.main_generator.update_macro_map(profile_data)
                        self.shadow_generator.update_macro_map(profile_data)

                        poc_price = profile_data['POC']['center']
                        logger.info(
                            f"🗺️ 地图更新完毕！当前核心引力区 (POC): {poc_price} | 发现 {len(profile_data['tradable_zones'])} 个交火区。")
                else:
                    logger.error("❌ 拉取 1m K线失败，沿用旧地图。")

            except Exception as e:
                logger.error(f"❌ 宏观地图更新异常: {e}")

            # 休息 5 分钟再画下一张
            await asyncio.sleep(300)

    async def _ws_tick_loop(self):
        """Tick 数据流协程：直连 OKX WebSocket 喂养高频引擎"""
        ws_url = "wss://ws.okx.com:8443/ws/v5/public"
        subscribe_payload = {
            "op": "subscribe",
            "args": [{"channel": "trades", "instId": self.symbol}]
        }

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

                                # 解析 Trades 频道数据
                                if "data" in data and isinstance(data["data"], list):
                                    for trade in data["data"]:
                                        # 转换成引擎认识的标准 Tick 格式
                                        # 转换成引擎认识的标准 Tick 格式
                                        tick = {
                                            'price': float(trade['px']),
                                            'size': float(trade['sz']),
                                            'side': trade['side'],
                                            'ts': int(trade['ts'])
                                        }

                                        # 🚀 优先级 1：主引擎同步处理 (最高优先级，严禁延迟)
                                        main_signal = self.main_generator.process_tick(tick)
                                        if main_signal:
                                            # 使用 create_task 异步处理信号执行，不阻塞 Tick 接收
                                            asyncio.create_task(self._handle_main_signal(main_signal))

                                        # 🚀 优先级 2：将 Tick 丢入影子队列 (非阻塞)
                                        try:
                                            self.shadow_queue.put_nowait(tick)
                                        except asyncio.QueueFull:
                                            # 如果队列满了，优先丢弃影子 Tick，确保主系统存活
                                            pass

                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning("⚠️ WebSocket 连接断开，准备重连...")
                                break

            except Exception as e:
                logger.error(f"❌ WebSocket 异常 ({e})，2 秒后重连...")
                await asyncio.sleep(2)

    async def _handle_main_signal(self, signal: dict):
        """处理微观引擎抛出的任何信号"""
        reason = signal.get('reason')
        action = signal.get('action')

        if reason == "TRIPLE_A_COMPLETE":
            # 🚀 抓到了完整的 A1-A2-A3 突破信号！
            if self.mode == "live":
                success = await self.execution_manager.execute_signal(signal)
                if not success:
                    # 如果实盘开仓因为余额等问题失败，必须手动把引擎状态重置回 IDLE
                    # 否则引擎会一直处于 LONG/SHORT 的幻觉中
                    self.main_generator._reset_to_idle()
            else:
                # 纸面交易 / 收集模式
                logger.info("=" * 50)
                logger.info(f"📝 [纸面收集] TripleA 信号触发！")
                logger.info(f"方向: {action} | 入场: {signal['entry_price']}")
                logger.info(f"止盈: {signal['take_profit']} | 止损: {signal['stop_loss']}")
                logger.info("=" * 50)

        elif action in ["CLOSE_LONG", "CLOSE_SHORT"]:
            # 🔄 极其精妙的架构呼应：
            # 本地引擎撞到了它自己算出来的 SL 或 TP。
            # 既然我们走的是交易所挂单路线，这代表着交易所那一端的真实订单大概率也成交了！
            # 我们不需要调用 API 去平仓，只打印一条日志，本地引擎已经自动重置为 IDLE。
            logger.info(f"🔄 本地引擎飞行状态终结 ({reason})，已准备好迎接下一轮交火。")

    async def _write_shadow_trade_to_csv(self, trade_data: dict, close_price: float, reason: str):
        """异步写入影子交易数据到CSV文件（使用线程池避免阻塞）"""
        import json
        entry_price = trade_data['Entry_Price']

        # 计算纯点数毛利
        if trade_data['Action'] == "BUY":
            gross_pnl = close_price - entry_price
        else:
            gross_pnl = entry_price - close_price

        # 序列化tradable_zones为JSON
        tradable_zones_json = json.dumps(
            trade_data.get('Tradable_Zones', []),
            default=str
        )

        # 准备行数据
        row = [
            # 基础信息
            trade_data['Entry_Time'],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            trade_data['Action'],
            entry_price,
            close_price,
            trade_data['SL_Price'],
            trade_data['TP_Price'],
            trade_data['Score'],
            reason,
            round(gross_pnl, 4),
            # 🆕 写入评价指标
            trade_data.get('MFE_Distance', 0),
            trade_data.get('MAE_Distance', 0),
            # 时间戳信息
            trade_data['Timestamps'].get('a1_start_time', 0),
            trade_data['Timestamps'].get('a1_end_time', 0),
            trade_data['Timestamps'].get('a2_start_time', 0),
            trade_data['Timestamps'].get('a2_end_time', 0),
            trade_data['Timestamps'].get('a3_start_time', 0),
            trade_data['Timestamps'].get('a3_end_time', 0),
            trade_data['Timestamps'].get('entry_time', 0),
            # CVD指标
            trade_data['CVD_Metrics'].get('global_cvd', 0),
            trade_data['CVD_Metrics'].get('global_volume', 0),
            trade_data['CVD_Metrics'].get('delta_ratio', 0),
            trade_data['CVD_Metrics'].get('recent_vol', 0),
            trade_data['CVD_Metrics'].get('recent_cvd', 0),
            trade_data['CVD_Metrics'].get('recent_delta_ratio', 0),
            # 诊断数据
            trade_data['Diagnostics'].get('current_box_size', 0),
            trade_data['Diagnostics'].get('vol_spike_threshold', 0),
            trade_data['Diagnostics'].get('delta_ratio_threshold', 0),
            # 交易区域信息
            tradable_zones_json
        ]

        # 使用线程池异步写入文件
        def write_to_file():
            with open(self.log_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)

        await asyncio.to_thread(write_to_file)
        logger.info(f"🚢 [科考打捞] 影子订单终结 ({reason})，毛利: {gross_pnl:.4f}，已异步写入 CSV。")

    async def _handle_shadow_signal(self, signal: dict):
        """👻 处理影子引擎的信号：只记录，不发单，直到订单完结写入 CSV"""
        import copy  # 确保导入 copy
        reason = signal.get('reason')
        action = signal.get('action')
        price = signal.get('price', signal.get('entry_price'))

        if reason == "TRIPLE_A_COMPLETE":
            self.shadow_active_trade = {
                'Entry_Time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'Action': action,
                'Entry_Price': price,
                'SL_Price': signal['stop_loss'],
                'TP_Price': signal['take_profit'],
                'Score': signal['signal_score'],
                'Timestamps': signal.get('timestamps', {}),
                'CVD_Metrics': signal.get('cvd_metrics', {}),
                'Diagnostics': signal.get('diagnostics', {}),
                # 🆕 极其关键：使用 deepcopy 锁定开仓那一刻的地图快照，防止后续被污染
                'Tradable_Zones': copy.deepcopy(self.shadow_generator.tradable_zones)
            }
            logger.debug(f"👻 [影子引擎] 虚拟开仓 {action} @ {price}")

        elif action in ["CLOSE_LONG", "CLOSE_SHORT"] and self.shadow_active_trade:
            # 🆕 提取刚刚计算出的 MFE/MAE
            self.shadow_active_trade['MFE_Distance'] = signal.get('mfe_distance', 0.0)
            self.shadow_active_trade['MAE_Distance'] = signal.get('mae_distance', 0.0)

            await self._write_shadow_trade_to_csv(self.shadow_active_trade, price, reason)
            self.shadow_active_trade = {}

    async def _shadow_engine_consumer(self):
        """
        👻 影子引擎消费者：运行在完全独立的协程中
        即使这里有任何计算延迟或磁盘IO延迟，都不会影响 WS 接收和主引擎
        """
        logger.info("🚢 影子科考船已启动，开始监听镜像 Tick 流...")
        while self._is_running:
            try:
                # 阻塞式等待队列中的 Tick
                tick = await self.shadow_queue.get()

                # 驱动影子引擎
                shadow_signal = self.shadow_generator.process_tick(tick)
                if shadow_signal:
                    await self._handle_shadow_signal(shadow_signal)

                # 标记处理完成
                self.shadow_queue.task_done()
            except Exception as e:
                logger.error(f"❌ 影子引擎内部异常: {e}")
                await asyncio.sleep(1)  # 发生异常避空，防止死循环轰炸日志


def main():
    parser = argparse.ArgumentParser(description="Momentum 1.66 - TripleA 四号引擎编排器")
    parser.add_argument('--symbol', type=str, default='ETH-USDT-SWAP', help='交易对，例如: ETH-USDT-SWAP')
    parser.add_argument('--mode', type=str, default='collect', choices=['collect', 'live'],
                        help="运行模式: 'collect' 或 'live'")
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
