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
import os
import signal
import sys
import time

import pandas as pd

# 确保能导入项目根目录的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy
from src.execution.trader import OKXTrader
from src.context.market_context import MarketContext
from config.loader import load_strategy_config
from src.utils.email_sender import send_trading_signal_email
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
                sleep_seconds = next_hour - now + 3

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

            df = df.iloc[:-1].copy()

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
            # ==========================================
            # 🛡️ 止损单挂单与重试机制 (最大重试 3 次)
            # ==========================================
            sl_algo_id = None
            max_retries = 3

            for attempt in range(max_retries):
                sl_algo_id = await self.trader.create_stop_loss_order(position_size, initial_stop_loss)
                if sl_algo_id:
                    logger.info(f"✅ 止损单创建成功，算法单ID: {sl_algo_id} (第 {attempt + 1} 次尝试)")
                    break  # 成功了就跳出循环

                # 如果失败了，但还没到最后一次，就休息一下再试
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ 止损单挂单失败，等待 1 秒后进行第 {attempt + 2} 次重试...")
                    await asyncio.sleep(1)  # 等待 1 秒让网络或交易所限流恢复

            # ==========================================
            # 💣 终极防御：重试耗尽后的核武器
            # ==========================================
            if not sl_algo_id:
                logger.error(f"❌ 连续 {max_retries} 次创建止损单失败！坚决拒绝裸奔，启动紧急平仓！")

                # 紧急调动市价反向减仓，把刚才买的直接卖掉
                if side == "buy":
                    await self.trader.market_sell(position_size, reduce_only=True)
                else:
                    await self.trader.market_buy(position_size, reduce_only=True)

                # 🌟 新增：发送紧急预警邮件！
                alert_details = (
                    f"⚠️ 警告！实盘开仓后，连续 {max_retries} 次无法在 OKX 挂出条件止损单！\n"
                    f"系统为了防止裸奔爆仓，已经触发了紧急市价平仓程序。\n"
                    f"请立即检查服务器网络或 OKX API 是否被限流！"
                )
                await send_trading_signal_email(
                    symbol=self.symbol,
                    signal_type="🚨 挂单失败 & 紧急平仓",
                    price=latest_close,
                    details=alert_details
                )

                return  # 退出执行，不记录这笔失败的持仓状态

            # 计算初始风险
            initial_risk = abs(latest_close - initial_stop_loss)

            # 记录持仓信息
            self._current_position = {
                'entry_price': latest_close,
                'position_size': position_size,
                'side': side,
                'entry_time': time.time(),
                'initial_stop_loss': initial_stop_loss,
                'current_stop_loss': initial_stop_loss,
                'initial_risk': initial_risk,
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

    async def _calculate_position_size(self, entry_price: float, stop_loss: float) -> int:
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
            position_size = int(position_size / ct_val)

            if position_size < 1:
                logger.error(f"❌ 资金不足以开哪怕 1 张合约！计算值: {position_size}")
                return 0

            logger.debug(
                f"📊 仓位计算: 可用={available_usdt:.2f}, 风险%={risk_pct}, 风险/合约={risk_per_contract:.4f}, 仓位={position_size:.2f}张")

            return position_size
        except Exception as e:
            logger.error(f"❌ 计算仓位大小异常: {e}")
            return 0

    async def _update_stop_loss(self, df: pd.DataFrame):
        """更新追踪止损（基于最新价格和 ATR）

        注意：完全依赖交易所条件止损单，不进行手动止损检测。
        当价格触发止损单时，交易所自动平仓，程序通过持仓状态变化检测平仓。
        """
        if not self._current_position:
            return

        try:
            # 🌟 新增：时间止损逻辑 (对齐回测)
            time_stop_hours = self.engine_cfg.get('time_stop', 48)  # 默认48小时
            hold_hours = (time.time() - self._current_position['entry_time']) / 3600

            if hold_hours >= time_stop_hours:
                entry_price = self._current_position['entry_price']
                initial_risk = self._current_position.get('initial_risk', 1.0)

                # 计算当前 MFE(R)
                if self._current_position['side'] == 'buy':
                    mfe_r = (self._current_position['highest_price'] - entry_price) / initial_risk
                else:
                    mfe_r = (entry_price - self._current_position['lowest_price']) / initial_risk

                if mfe_r < 1.0:
                    logger.warning(f"⏳ [时间止损] 仓位钝化！持仓 {hold_hours:.1f} 小时未触及 1R，主动撤退！")
                    await self._close_position("time_stop", df.iloc[-1]['close'])
                    return

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

            # 更新最高价和最低价（用于时间止损计算）
            self._current_position['highest_price'] = max(self._current_position['highest_price'], latest_high)
            self._current_position['lowest_price'] = min(self._current_position['lowest_price'], latest_low)

            new_stop_loss = current_stop_loss

            # 根据仓位方向更新止损（提灯止损逻辑）
            if side == "buy":
                # 多头：止损上移，不低于当前止损价
                new_stop_loss = max(current_stop_loss, latest_close - latest_atr * atr_multiplier)
            else:
                # 空头：止损下移，不高于当前止损价
                new_stop_loss = min(current_stop_loss, latest_close + latest_atr * atr_multiplier)

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

                    if old_algo_id:
                        logger.info(f"🔧 取消旧止损单: {old_algo_id}")
                        await self.trader.cancel_algo_order(old_algo_id)

                    # 🌟 必须算出移动止损的方向
                    sl_side = "sell" if side == "buy" else "buy"

                    # ==========================================
                    # 🛡️ 遗漏的重点：移动止损的重试机制与裸奔报警！
                    # ==========================================
                    new_algo_id = None
                    for attempt in range(3):
                        new_algo_id = await self.trader.create_stop_loss_order(position_size, new_stop_loss,
                                                                               sl_side)
                        if new_algo_id:
                            break
                        if attempt < 2:
                            logger.warning(f"⚠️ 更新止损单挂单失败，1秒后重试 (第 {attempt + 2} 次)...")
                            await asyncio.sleep(1)

                    if new_algo_id:
                        self._current_position['sl_algo_id'] = new_algo_id
                        logger.info(f"✅ 止损单更新成功，新算法单ID: {new_algo_id}")
                    else:
                        logger.error("❌ 严重警告：连续 3 次创建新止损单失败！仓位已处于无止损裸奔状态！")
                        # 🌟 遗漏的重点：发送紧急夺命连环 Call 邮件
                        alert_details = (
                            f"⚠️ 紧急警报！实盘系统在更新追踪止损时，连续 3 次无法在 OKX 挂出新的条件单！\n"
                            f"目前的旧止损单已经被撤销，您的仓位正处于【完全无止损保护】的裸奔状态！\n"
                            f"请立即登录 OKX APP 手动接管仓位，或检查服务器网络状态！"
                        )
                        await send_trading_signal_email(
                            symbol=self.symbol,
                            signal_type="🚨 追踪止损更新失败 (裸奔警告)",
                            price=latest_close,
                            details=alert_details
                        )

        except Exception as e:
            logger.error(f"❌ 更新止损异常: {e}")

    async def _close_position(self, reason: str, close_price: float):
        """手动平仓（用于时间止损或其他手动平仓情况）

        注意：正常止损平仓由交易所条件止损单自动处理，不调用此方法。
        """
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
            pnl_pct = (close_price - entry_price) / entry_price * 100 if side == 'buy' else (
                                                                                                        entry_price - close_price) / entry_price * 100
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
                    close_result = await self.trader.market_sell(position_size, reduce_only=True)
                else:
                    # 空头平仓：市价买入
                    close_result = await self.trader.market_buy(position_size, reduce_only=True)

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
        """持仓监控循环（状态同步）

        通过定期检查持仓状态是否一致，检测交易所自动平仓（止损触发或手动平仓）。
        不进行手动止损检测，完全依赖交易所条件止损单。
        """
        try:
            while self._is_running:
                if self._current_position and not self.context.is_in_position:
                    # 本地有持仓记录，但市场上下文显示无持仓
                    # 说明持仓可能已被交易所平仓（止损触发或手动平仓）
                    logger.warning("🔄 检测到持仓状态不一致，清理本地持仓记录")
                    self._current_position = None
                await asyncio.sleep(5)  # 每分钟检查一次
        except asyncio.CancelledError:
            logger.info("📊 持仓监控任务被取消")
        except Exception as e:
            logger.error(f"❌ 持仓监控异常: {e}")



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
