#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SMC 二号引擎编排器 (SMCOrchestrator)
将回测 SMC 策略转化为实盘策略，每小时执行一次，使用单向数据流。

设计原则：
1. 单向数据流：DataFeed → Indicators → Strategy → RiskManager → Execution
2. 状态集中：MarketContext 作为唯一状态源
3. 配置驱动：所有参数从 SMC 配置读取
4. 定时触发：每小时 00 分 00 秒拉取 K 线数据

数据流：
1. 每小时拉取最新 K 线数据（可配置数量）
2. 计算 SMC 指标（ATR、EMA、ADX 等）
3. 运行 SMCStrategy 生成信号
4. 如果未持仓且有信号，执行开仓
5. 如果已持仓，持续监控并更新追踪止损

风险管理：
- 沿用回测引擎的 ATR 追踪止损逻辑
- 仓位大小基于风险百分比和初始止损计算
"""
import argparse
import asyncio
import signal
import sys
import os
import time
import pandas as pd
from typing import Dict, Any, Optional

# 确保能导入项目根目录的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy
from src.execution.trader import OKXTrader, ExecutionResult
from src.context.market_context import MarketContext
from config.loader import load_strategy_config
from src.utils.log import get_logger

logger = get_logger(__name__)


class SMCOrchestrator:
    """SMC 策略编排器"""

    def __init__(self, symbol: str = "ETH-USDT-SWAP", mode: str = "collect"):
        """
        初始化编排器

        Args:
            symbol: 交易对符号
            mode: 运行模式 ('collect' 或 'live')
        """
        self.symbol = symbol
        self.mode = mode

        # 加载 SMC 配置
        try:
            self.config = load_strategy_config("smc", symbol)
            logger.info(f"✅ 成功加载 {symbol} SMC 配置")
        except Exception as e:
            logger.error(f"❌ 加载配置失败: {e}")
            raise

        # 提取配置
        self.timeframe = self.config.get('timeframe', '1H')
        self.strat_cfg = self.config.get('strategy', {})
        self.engine_cfg = self.config.get('engine', {})
        self.ai_cfg = self.config.get('ai_filter', {})

        # 创建线程安全的市场上下文
        self.context = MarketContext()

        # 初始化核心组件
        self.data_loader = OKXDataLoader(symbol=symbol, timeframe=self.timeframe)
        self.strategy = SMCStrategy(
            ema_period=self.strat_cfg.get('ema_period', 144),
            lookback=self.strat_cfg.get('lookback', 15),
            atr_mult=self.strat_cfg.get('atr_mult', 1.5),
            ob_expiry=self.strat_cfg.get('ob_expiry', 72),
            sl_buffer=self.strat_cfg.get('sl_buffer', 0.6),
            entry_buffer=self.strat_cfg.get('entry_buffer', -0.1),
            ai_config={
                'enabled': self.ai_cfg.get('enabled', False),
                'model_path': self.ai_cfg.get('model_path'),
                'threshold': self.ai_cfg.get('threshold', 0.35)
            }
        )

        # 实盘交易器（仅在 live 模式下真正执行）
        self.trader = OKXTrader(
            symbol=symbol,
            leverage=50,  # 默认杠杆，可根据配置调整
            risk_pct=self.engine_cfg.get('max_risk', 0.02),  # 使用 max_risk 作为风险百分比
            sl_pct=0.0015,  # 默认止损百分比，实际使用 ATR 追踪止损
            context=self.context
        )

        # 运行时状态
        self._is_running = False
        self._hourly_task = None
        self._monitor_task = None
        self._current_position = None  # 当前持仓信息
        self._current_stop_loss = 0.0  # 当前止损价
        self._current_atr = 0.0  # 当前 ATR 值

        logger.info(f"🚀 SMC 编排器初始化完成: {symbol} [{mode.upper()}]")

    async def run(self):
        """启动编排器主循环"""
        logger.info("🚀 启动 SMC 引擎编排器...")
        self._is_running = True

        # 启动交易器余额更新循环（仅实盘模式）
        if self.mode == "live":
            asyncio.create_task(self.trader.update_balance_loop())

        # 启动每小时定时任务
        self._hourly_task = asyncio.create_task(self._hourly_loop())

        # 启动持仓监控任务
        self._monitor_task = asyncio.create_task(self._monitor_position_loop())

        logger.info("✅ SMC 引擎编排器已上线！")

    async def shutdown(self):
        """安全关闭编排器"""
        logger.info("🔔 正在安全关闭 SMC 编排器...")
        self._is_running = False

        # 取消任务
        if self._hourly_task:
            self._hourly_task.cancel()
        if self._monitor_task:
            self._monitor_task.cancel()

        # 清理资源
        if self.context.is_in_position:
            logger.warning("⚠️ 关闭时仍有持仓，请手动处理")

        logger.info("✅ SMC 编排器已安全关闭")

    async def _hourly_loop(self):
        """每小时执行的主任务"""
        try:
            while self._is_running:
                # 计算到下一个整点小时的时间
                now = time.time()
                next_hour = ((now // 3600) + 1) * 3600
                sleep_seconds = next_hour - now

                logger.info(f"⏰ 下一个执行时间: {time.ctime(next_hour)} ({sleep_seconds:.0f} 秒后)")
                await asyncio.sleep(sleep_seconds)

                # 执行每小时任务
                await self._hourly_task_execution()
        except asyncio.CancelledError:
            logger.info("⏰ 每小时任务被取消")
        except Exception as e:
            logger.error(f"❌ 每小时任务异常: {e}")

    async def _hourly_task_execution(self):
        """每小时任务的具体执行逻辑"""
        logger.info("🕐 开始每小时 SMC 信号扫描...")

        try:
            # 1. 拉取最新的 K 线数据（例如最近 500 根）
            df = self.data_loader.fetch_historical_data(limit=500)
            if df.empty:
                logger.error("❌ 获取数据失败，跳过本次扫描")
                return

            # 2. 计算 SMC 指标
            df = add_smc_indicators(df)
            if df.empty:
                logger.error("❌ 计算指标失败，跳过本次扫描")
                return

            # 3. 生成信号
            df = self.strategy.generate_signals(df)

            # 获取最新的信号（最后一行）
            latest_signal = df.iloc[-1]['Signal']
            latest_sl_price = df.iloc[-1]['SL_Price'] if 'SL_Price' in df.columns else None
            latest_atr = df.iloc[-1]['ATR'] if 'ATR' in df.columns else None

            # 更新上下文中的 ATR 值（用于风险管理）
            if latest_atr is not None:
                self._current_atr = latest_atr

            logger.info(f"📊 最新信号: {latest_signal}, 止损价: {latest_sl_price}, ATR: {latest_atr}")

            # 4. 检查持仓状态
            if self.context.is_in_position:
                logger.debug("📦 当前有持仓，跳过开仓逻辑")
                # 更新止损价（如果需要）
                await self._update_stop_loss(df)
            else:
                # 5. 无持仓，检查是否有交易信号
                if latest_signal != 0:
                    logger.warning(f"🚨 检测到交易信号: {latest_signal}")
                    await self._execute_trade(df, latest_signal, latest_sl_price)
                else:
                    logger.debug("📭 无交易信号，继续等待")

        except Exception as e:
            logger.error(f"❌ 每小时任务执行异常: {e}")

    async def _execute_trade(self, df: pd.DataFrame, signal: int, sl_price: float):
        """执行交易开仓"""
        if self.mode != "live":
            logger.info(f"📝 [收集模式] 检测到信号但不执行: signal={signal}, sl_price={sl_price}")
            return

        logger.warning(f"🔫 [实盘模式] 准备执行交易: signal={signal}")

        try:
            # 获取最新价格
            latest_close = df.iloc[-1]['close']

            # 计算仓位大小
            position_size = await self._calculate_position_size(latest_close, sl_price)
            if position_size <= 0:
                logger.error("❌ 仓位大小计算失败，跳过开仓")
                return

            # 执行市价开仓
            side = "buy" if signal == 1 else "sell"
            logger.warning(f"🎯 执行 {side} 开仓: 价格={latest_close}, 仓位={position_size}")

            # 调用 trader 开仓方法
            if side == "buy":
                order_result = await self.trader.market_buy(position_size)
            else:
                order_result = await self.trader.market_sell(position_size)

            if not order_result or order_result.get('code') != '0':
                logger.error(f"❌ 开仓失败: {order_result}")
                return

            order_data = order_result.get('data', [{}])[0]
            order_id = order_data.get('ordId')
            logger.info(f"✅ 开仓订单提交成功，订单ID: {order_id}")

            # 开仓后设置初始止损
            # 对于 SMC 策略，sl_price 是策略计算的止损价
            if sl_price is not None and not pd.isna(sl_price):
                initial_stop_loss = sl_price
            else:
                # 使用 ATR 追踪止损
                atr_multiplier = self.engine_cfg.get('atr_multiplier', 7.0)
                initial_stop_loss = latest_close - self._current_atr * atr_multiplier if signal == 1 else latest_close + self._current_atr * atr_multiplier

            # 创建止损单
            sl_algo_id = await self.trader.create_stop_loss_order(position_size, initial_stop_loss)
            if not sl_algo_id:
                logger.error("❌ 创建止损单失败")
                # 可以考虑取消开仓订单，但暂时只记录日志
            else:
                logger.info(f"✅ 止损单创建成功，算法单ID: {sl_algo_id}")

            # 记录持仓信息
            self._current_position = {
                'entry_price': latest_close,
                'position_size': position_size,
                'side': side,
                'entry_time': time.time(),
                'initial_stop_loss': initial_stop_loss,
                'current_stop_loss': initial_stop_loss,
                'order_id': order_id,
                'sl_algo_id': sl_algo_id,
                'highest_price': latest_close,
                'lowest_price': latest_close
            }

            # 更新市场上下文
            self.context.update_position({
                "symbol": self.symbol,
                "side": side,
                "size": position_size,
                "entry_price": latest_close,
                "current_price": latest_close,
                "unrealized_pnl": 0.0,
                "leverage": 50,
                "stop_loss_price": initial_stop_loss,
                "take_profit_price": 0.0,  # SMC 策略使用追踪止损，不设止盈
                "initial_stop_loss": initial_stop_loss
            })

            logger.info(f"✅ 开仓成功！入场价: {latest_close}, 止损价: {initial_stop_loss}")

        except Exception as e:
            logger.error(f"❌ 执行交易异常: {e}")

    async def _calculate_position_size(self, entry_price: float, stop_loss: float) -> float:
        """计算仓位大小（基于风险百分比）"""
        try:
            # 获取可用余额
            available_usdt = self.trader.available_usdt
            if available_usdt <= 0:
                # 如果未获取到余额，使用初始资本
                initial_capital = self.engine_cfg.get('initial_capital', 1000.0)
                logger.warning(f"⚠️ 未获取到可用余额，使用初始资本: {initial_capital}")
                available_usdt = initial_capital

            # 风险百分比
            risk_pct = self.engine_cfg.get('max_risk', 0.02)

            # 计算每张合约的风险金额
            risk_per_contract = abs(entry_price - stop_loss) if stop_loss is not None else entry_price * 0.01

            if risk_per_contract <= 0:
                logger.error("❌ 风险金额计算无效")
                return 0

            # 计算仓位大小（合约张数）
            risk_amount = available_usdt * risk_pct
            position_size = risk_amount / risk_per_contract

            # 根据合约面值调整
            ct_val = self.trader.ct_val_map.get(self.symbol, 0.1)
            position_size = position_size / ct_val

            logger.debug(f"📊 仓位计算: 可用={available_usdt:.2f}, 风险%={risk_pct}, 风险/合约={risk_per_contract:.4f}, 仓位={position_size:.2f}张")

            return position_size
        except Exception as e:
            logger.error(f"❌ 计算仓位大小异常: {e}")
            return 0

    async def _update_stop_loss(self, df: pd.DataFrame):
        """更新追踪止损（基于最新价格和 ATR）"""
        if not self._current_position:
            return

        try:
            latest_close = df.iloc[-1]['close']
            latest_high = df.iloc[-1]['high']
            latest_low = df.iloc[-1]['low']
            latest_atr = df.iloc[-1]['ATR'] if 'ATR' in df.columns else self._current_atr

            if latest_atr <= 0:
                logger.warning("⚠️ ATR 值无效，跳过止损更新")
                return

            side = self._current_position['side']
            current_stop_loss = self._current_position['current_stop_loss']
            atr_multiplier = self.engine_cfg.get('atr_multiplier', 7.0)

            new_stop_loss = current_stop_loss

            # 根据仓位方向更新止损
            if side == "buy":
                # 多头：止损上移，不低于当前止损价
                new_stop_loss = max(current_stop_loss, latest_close - latest_atr * atr_multiplier)
                # 检查是否触发止损
                if latest_low <= current_stop_loss:
                    logger.warning(f"🛑 触发止损！当前价={latest_low}, 止损价={current_stop_loss}")
                    await self._close_position("stop_loss", latest_close)
                    return
            else:
                # 空头：止损下移，不高于当前止损价
                new_stop_loss = min(current_stop_loss, latest_close + latest_atr * atr_multiplier)
                # 检查是否触发止损
                if latest_high >= current_stop_loss:
                    logger.warning(f"🛑 触发止损！当前价={latest_high}, 止损价={current_stop_loss}")
                    await self._close_position("stop_loss", latest_close)
                    return

            # 如果止损价有变化，更新止损单
            if abs(new_stop_loss - current_stop_loss) > 0.0001:
                logger.info(f"📈 更新止损价: {current_stop_loss:.4f} -> {new_stop_loss:.4f}")
                self._current_position['current_stop_loss'] = new_stop_loss

                # 更新市场上下文
                if self.context.position_info:
                    self.context.position_info.stop_loss_price = new_stop_loss

                # 实盘模式下更新交易所止损单
                if self.mode == "live":
                    position_size = self._current_position['position_size']
                    old_algo_id = self._current_position.get('sl_algo_id')

                    # 取消旧止损单
                    if old_algo_id:
                        logger.info(f"🔧 取消旧止损单: {old_algo_id}")
                        await self.trader.cancel_algo_order(old_algo_id)

                    # 创建新止损单
                    new_algo_id = await self.trader.create_stop_loss_order(position_size, new_stop_loss)
                    if new_algo_id:
                        self._current_position['sl_algo_id'] = new_algo_id
                        logger.info(f"✅ 止损单更新成功，新算法单ID: {new_algo_id}")
                    else:
                        logger.error("❌ 创建新止损单失败")

        except Exception as e:
            logger.error(f"❌ 更新止损异常: {e}")

    async def _close_position(self, reason: str, close_price: float):
        """平仓"""
        logger.warning(f"🏁 平仓: 原因={reason}, 价格={close_price}")

        if not self._current_position:
            self.context.clear_position()
            return

        try:
            side = self._current_position['side']
            position_size = self._current_position['position_size']
            entry_price = self._current_position['entry_price']
            sl_algo_id = self._current_position.get('sl_algo_id')

            # 计算盈亏
            pnl_pct = (close_price - entry_price) / entry_price * 100 if side == 'buy' else (entry_price - close_price) / entry_price * 100
            logger.info(f"💰 平仓盈亏: {pnl_pct:.2f}%")

            # 实盘模式下执行平仓操作
            if self.mode == "live":
                # 取消止损单
                if sl_algo_id:
                    logger.info(f"🔧 取消止损单: {sl_algo_id}")
                    await self.trader.cancel_algo_order(sl_algo_id)

                # 市价平仓
                logger.warning(f"🔫 执行市价平仓: {side} -> {position_size}张")
                if side == "buy":
                    # 多头平仓：市价卖出
                    close_result = await self.trader.market_sell(position_size)
                else:
                    # 空头平仓：市价买入
                    close_result = await self.trader.market_buy(position_size)

                if close_result and close_result.get('code') == '0':
                    logger.info(f"✅ 平仓成功！订单ID: {close_result.get('data', [{}])[0].get('ordId')}")
                else:
                    logger.error(f"❌ 平仓失败: {close_result}")

            # 清空持仓状态
            self._current_position = None
            self.context.clear_position()

        except Exception as e:
            logger.error(f"❌ 平仓异常: {e}")
            # 无论如何清空状态
            self._current_position = None
            self.context.clear_position()

    async def _monitor_position_loop(self):
        """持仓监控循环（更频繁地检查止损）"""
        try:
            while self._is_running:
                if self.context.is_in_position:
                    # 每分钟检查一次止损
                    await self._check_stop_loss()
                await asyncio.sleep(60)  # 每分钟检查一次
        except asyncio.CancelledError:
            logger.info("📊 持仓监控任务被取消")
        except Exception as e:
            logger.error(f"❌ 持仓监控异常: {e}")

    async def _check_stop_loss(self):
        """检查止损触发"""
        if not self._current_position:
            return

        try:
            # 获取当前价格（简单方法：拉取最新 K 线）
            # 注意：这里应该使用 tick 数据，但为了简单先使用 K 线
            df = self.data_loader.fetch_historical_data(limit=2)
            if df.empty:
                return

            latest_close = df.iloc[-1]['close']
            latest_high = df.iloc[-1]['high']
            latest_low = df.iloc[-1]['low']

            side = self._current_position['side']
            stop_loss = self._current_position['current_stop_loss']

            # 检查止损触发
            if side == "buy" and latest_low <= stop_loss:
                logger.warning(f"🛑 监控发现止损触发！当前最低价={latest_low}, 止损价={stop_loss}")
                await self._close_position("stop_loss", stop_loss)
            elif side == "sell" and latest_high >= stop_loss:
                logger.warning(f"🛑 监控发现止损触发！当前最高价={latest_high}, 止损价={stop_loss}")
                await self._close_position("stop_loss", stop_loss)

        except Exception as e:
            logger.error(f"❌ 检查止损异常: {e}")


def main():
    """主函数：命令行入口"""
    parser = argparse.ArgumentParser(description="Momentum 1.66 - SMC 二号引擎编排器")
    parser.add_argument('--symbol', type=str, default='ETH-USDT-SWAP',
                        help='交易对符号，例如: ETH-USDT-SWAP, BTC-USDT-SWAP')
    parser.add_argument('--mode', type=str, default='collect',
                        choices=['collect', 'live'],
                        help="运行模式: 'collect' (只收集信号) 或 'live' (实盘自动交易)")
    args = parser.parse_args()

    # 创建编排器实例
    orchestrator = SMCOrchestrator(symbol=args.symbol, mode=args.mode)
    logger.info(f"⚙️ 当前引擎运行模式: 【{args.mode.upper()}】")

    # 优雅重启，监听 kill -15
    def handle_sigterm(*args):
        logger.warning("🔔 收到 kill -15 信号！转换为安全迫降指令...")
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.warning("🔔 收到停止指令！准备安全迫降...")
        asyncio.run(orchestrator.shutdown())


if __name__ == "__main__":
    main()