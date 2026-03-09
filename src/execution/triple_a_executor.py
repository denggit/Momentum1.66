#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Triple-A专用执行器
处理Triple-A信号的执行逻辑
"""
import asyncio
import time
from typing import Dict, Any, Optional

from src.strategy.triple_a.config import TripleAConfig
from src.context.market_context import MarketContext
from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleAExecutor:
    """Triple-A专用执行器"""

    def __init__(self, config: TripleAConfig, context: MarketContext, trader=None):
        self.config = config
        self.context = context
        self.trader = trader  # OKXTrader实例，可选

        # 手续费配置（买入0.05% + 卖出0.05% = 总0.1%）
        self.total_commission_pct = 0.001

        # 交易统计
        self.stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0,
            "failed_auction_stops": 0
        }

        logger.info("🚀 Triple-A执行器初始化完成")

    async def execute_triple_a(self, signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        执行Triple-A交易

        Args:
            signal: Triple-A信号，包含phase、direction、price等信息

        Returns:
            交易执行结果字典，如果未执行返回None
        """
        signal_type = signal.get('type', '')
        phase = signal.get('phase', '')
        direction = signal.get('direction', '')

        if signal_type == "AGGRESSION_TRIGGERED":
            return await self._execute_aggression_trade(signal)
        elif signal_type == "FAILED_AUCTION_DETECTED":
            return await self._execute_failed_auction_stop(signal)
        else:
            logger.debug(f"⚠️  忽略非交易信号: {signal_type}")
            return None

    async def _execute_aggression_trade(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """执行Aggression交易"""
        direction = signal.get('direction', 'UNKNOWN')
        entry_price = signal.get('price', 0)
        accumulation_low = signal.get('accumulation_low', 0)
        accumulation_high = signal.get('accumulation_high', 0)

        if direction not in ['UP', 'DOWN']:
            logger.error(f"❌ 无效的交易方向: {direction}")
            return None

        # 先计算止损和止盈（仓位计算需要准确的止损价）
        stop_loss_price, take_profit_price = self._calculate_stop_take(
            entry_price, direction, accumulation_low, accumulation_high
        )

        # 检查止损计算是否有效（可能因为风险过大返回None）
        if stop_loss_price is None or take_profit_price is None:
            logger.warning(f"⚠️  止损计算无效，跳过交易（方向: {direction}）")
            return None

        # 计算仓位大小（使用实际的止损价）
        position_size = self._calculate_position_size(
            entry_price, direction, stop_loss_price
        )

        # 检查风险回报比
        reward_ratio = self._calculate_reward_ratio(
            entry_price, stop_loss_price, take_profit_price, direction
        )

        if reward_ratio < self.config.min_reward_ratio:
            logger.warning(f"⚠️  风险回报比过低: {reward_ratio:.2f} < {self.config.min_reward_ratio}, 跳过交易")
            return None

        # 执行交易（模拟或实盘）
        trade_result = await self._place_order(
            direction=direction,
            entry_price=entry_price,
            position_size=position_size,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price
        )

        if trade_result:
            self.stats["total_trades"] += 1
            logger.warning(f"✅ 执行Aggression交易成功！方向: {direction}, "
                          f"入场价: {entry_price:.2f}, 仓位: {position_size:.2f}")

            # 更新上下文中的持仓信息
            self._update_position_in_context(trade_result)

        return trade_result

    async def _execute_failed_auction_stop(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """执行Failed Auction止损"""
        logger.error(f"🛑 执行Failed Auction止损！价格: {signal.get('price', 0):.2f}")

        # 检查当前是否有持仓
        current_position = self.context.get_position()
        if not current_position:
            logger.warning("⚠️  没有持仓需要止损")
            return None

        # 计算止损价格（市价平仓）
        stop_price = signal.get('price', 0)

        # 执行止损（模拟或实盘）
        stop_result = await self._close_position(
            position=current_position,
            stop_price=stop_price,
            reason="FAILED_AUCTION"
        )

        if stop_result:
            self.stats["failed_auction_stops"] += 1
            logger.error(f"✅ Failed Auction止损执行成功！亏损: ${stop_result.get('pnl', 0):.2f}")

        return stop_result

    def _calculate_position_size(self, entry_price: float, direction: str,
                                 stop_loss_price: float) -> float:
        """计算仓位大小（基于风险管理）"""
        # 获取账户余额
        account_balance = self._get_account_balance()
        risk_amount = account_balance * self.config.risk_pct

        # 计算价格风险（基于止损距离）
        price_risk = abs(entry_price - stop_loss_price)

        # 每张合约风险金额
        risk_per_contract = price_risk * self.config.contract_size

        if risk_per_contract <= 0:
            return 0

        # 基于风险金额计算合约数量
        contract_count_by_risk = risk_amount / risk_per_contract

        # 基于杠杆计算最大可开合约数量
        margin_per_contract = entry_price * self.config.contract_size / self.config.leverage
        max_contracts_by_margin = account_balance / margin_per_contract

        # 取两者最小值
        position_size = min(contract_count_by_risk, max_contracts_by_margin)

        # 应用风控限制
        position_size = min(position_size, self.config.max_position_limit)
        position_size = max(position_size, self.config.min_trade_unit)

        return position_size

    def _get_account_balance(self) -> float:
        """获取账户余额（USDT）"""
        # 如果有trader实例，尝试从trader获取
        if self.trader and hasattr(self.trader, 'get_balance'):
            try:
                return self.trader.get_balance()
            except:
                pass

        # 否则使用研究配置中的初始余额
        if hasattr(self.config, 'research_initial_balance'):
            return self.config.research_initial_balance

        # 默认值（20U小账户）
        return 20.0

    def _calculate_stop_take(self, entry_price: float, direction: str,
                             accumulation_low: float, accumulation_high: float) -> tuple:
        """
        计算止损和止盈价格（结构型止损）

        采用Fabio Valentini的结构型止损方法：
        1. 止损设置在累积区间边界外一个小的缓冲距离（0.05%）
        2. 计算实际总风险（价格风险+手续费）
        3. 如果总风险超过initial_sl_pct（最大允许风险），则返回None跳过交易
        4. 止盈基于实际价格风险和最小风险回报比

        Returns:
            tuple: (stop_loss_price, take_profit_price) 或 (None, None) 如果风险过大
        """
        # 总手续费率：买入0.05% + 卖出0.05% = 0.1%
        total_commission_pct = self.total_commission_pct

        # 结构止损缓冲（防止市场噪音触发止损）
        buffer_pct = 0.0005  # 0.05%

        if direction == "UP":
            # 结构型止损：在累积区间低点下方设置止损
            stop_loss_price = accumulation_low * (1 - buffer_pct)

            # 确保止损价不高于入场价（安全保护）
            if stop_loss_price >= entry_price:
                stop_loss_price = entry_price * (1 - buffer_pct)
                logger.warning(f"⚠️  累积区间低点{accumulation_low:.2f}高于/等于入场价{entry_price:.2f}，使用基于入场价的止损")

            # 计算实际价格风险
            price_risk = entry_price - stop_loss_price
            if price_risk <= 0:
                logger.error(f"❌ 价格风险计算错误: entry={entry_price:.2f}, sl={stop_loss_price:.2f}")
                return None, None

            # 计算实际总风险比例（价格风险比例 + 手续费比例）
            price_risk_pct = price_risk / entry_price
            actual_total_risk_pct = price_risk_pct + total_commission_pct

            # 检查总风险是否超过最大允许风险（initial_sl_pct）
            if actual_total_risk_pct > self.config.initial_sl_pct:
                logger.warning(f"⚠️  结构止损风险过大: {actual_total_risk_pct*100:.3f}% > {self.config.initial_sl_pct*100:.3f}%，跳过交易")
                return None, None

            # 计算止盈价（基于实际价格风险和最小风险回报比）
            take_profit_price = entry_price + price_risk * self.config.min_reward_ratio

            logger.debug(f"结构止损计算（多头）: 入场={entry_price:.2f}, 止损={stop_loss_price:.2f}, "
                        f"价格风险={price_risk_pct*100:.3f}%, 总风险={actual_total_risk_pct*100:.3f}%")

        else:  # DOWN
            # 结构型止损：在累积区间高点上方设置止损
            stop_loss_price = accumulation_high * (1 + buffer_pct)

            # 确保止损价不低于入场价（安全保护）
            if stop_loss_price <= entry_price:
                stop_loss_price = entry_price * (1 + buffer_pct)
                logger.warning(f"⚠️  累积区间高点{accumulation_high:.2f}低于/等于入场价{entry_price:.2f}，使用基于入场价的止损")

            # 计算实际价格风险
            price_risk = stop_loss_price - entry_price
            if price_risk <= 0:
                logger.error(f"❌ 价格风险计算错误: entry={entry_price:.2f}, sl={stop_loss_price:.2f}")
                return None, None

            # 计算实际总风险比例
            price_risk_pct = price_risk / entry_price
            actual_total_risk_pct = price_risk_pct + total_commission_pct

            # 检查总风险是否超过最大允许风险
            if actual_total_risk_pct > self.config.initial_sl_pct:
                logger.warning(f"⚠️  结构止损风险过大: {actual_total_risk_pct*100:.3f}% > {self.config.initial_sl_pct*100:.3f}%，跳过交易")
                return None, None

            # 计算止盈价
            take_profit_price = entry_price - price_risk * self.config.min_reward_ratio

            logger.debug(f"结构止损计算（空头）: 入场={entry_price:.2f}, 止损={stop_loss_price:.2f}, "
                        f"价格风险={price_risk_pct*100:.3f}%, 总风险={actual_total_risk_pct*100:.3f}%")

        return stop_loss_price, take_profit_price

    def _calculate_reward_ratio(self, entry_price: float, stop_loss_price: float,
                                take_profit_price: float, direction: str) -> float:
        """计算净风险回报比（包含手续费）"""
        # 总手续费率：买入0.05% + 卖出0.05% = 0.1%
        total_commission_pct = self.total_commission_pct

        if direction == "UP":
            # 价格风险（价格下跌到止损）
            price_risk = entry_price - stop_loss_price
            # 价格回报（价格上涨到止盈）
            price_reward = take_profit_price - entry_price
            # 净风险 = 价格风险 + 入场手续费 + 出场手续费（止损时）
            # 入场手续费基于entry_price，出场手续费基于stop_loss_price
            net_risk = price_risk + (entry_price * total_commission_pct) + (stop_loss_price * total_commission_pct)
            # 净回报 = 价格回报 - 入场手续费 - 出场手续费（止盈时）
            net_reward = price_reward - (entry_price * total_commission_pct) - (take_profit_price * total_commission_pct)
        else:  # DOWN
            price_risk = stop_loss_price - entry_price
            price_reward = entry_price - take_profit_price
            net_risk = price_risk + (entry_price * total_commission_pct) + (stop_loss_price * total_commission_pct)
            net_reward = price_reward - (entry_price * total_commission_pct) - (take_profit_price * total_commission_pct)

        if net_risk <= 0:
            return 0

        return net_reward / net_risk

    async def _place_order(self, direction: str, entry_price: float, position_size: float,
                           stop_loss_price: float, take_profit_price: float) -> Dict[str, Any]:
        """下单（模拟或实盘）"""
        # 检查是否有真实的trader
        if self.trader:
            # 实盘下单
            try:
                order_result = await self.trader.place_order(
                    symbol=self.config.symbol,
                    side="buy" if direction == "UP" else "sell",
                    size=position_size,
                    price=entry_price,
                    stop_loss=stop_loss_price,
                    take_profit=take_profit_price
                )
                return order_result
            except Exception as e:
                logger.error(f"❌ 下单失败: {e}")
                return None
        else:
            # 模拟下单
            trade_id = f"sim_{int(time.time())}_{direction.lower()}"

            trade_result = {
                'trade_id': trade_id,
                'symbol': self.config.symbol,
                'direction': direction,
                'entry_price': entry_price,
                'position_size': position_size,
                'stop_loss_price': stop_loss_price,
                'take_profit_price': take_profit_price,
                'entry_time': time.time(),
                'status': 'OPEN',
                'is_simulated': True
            }

            return trade_result

    async def _close_position(self, position: Dict[str, Any], stop_price: float,
                              reason: str = "STOP_LOSS") -> Dict[str, Any]:
        """平仓（模拟或实盘）"""
        # 检查是否有真实的trader
        if self.trader:
            # 实盘平仓
            try:
                close_result = await self.trader.close_position(
                    symbol=position.get('symbol', self.config.symbol),
                    position_id=position.get('position_id')
                )
                return close_result
            except Exception as e:
                logger.error(f"❌ 平仓失败: {e}")
                return None
        else:
            # 模拟平仓
            entry_price = position.get('entry_price', 0)
            position_size = position.get('size', 0)
            direction = position.get('side', 'long')

            # 计算盈亏
            if direction == "long":
                pnl = (stop_price - entry_price) * position_size * self.config.contract_size
            else:  # short
                pnl = (entry_price - stop_price) * position_size * self.config.contract_size

            close_result = {
                'position_id': position.get('position_id', 'sim_position'),
                'exit_price': stop_price,
                'exit_time': time.time(),
                'pnl': pnl,
                'pnl_pct': pnl / (entry_price * position_size * self.config.contract_size) * 100,
                'reason': reason,
                'is_simulated': True
            }

            # 更新统计
            if pnl > 0:
                self.stats["winning_trades"] += 1
            else:
                self.stats["losing_trades"] += 1

            self.stats["total_pnl"] += pnl

            return close_result

    def _update_position_in_context(self, trade_result: Dict[str, Any]):
        """更新上下文中的持仓信息"""
        position_info = {
            'symbol': trade_result.get('symbol', self.config.symbol),
            'side': 'long' if trade_result.get('direction') == 'UP' else 'short',
            'size': trade_result.get('position_size', 0),
            'entry_price': trade_result.get('entry_price', 0),
            'current_price': trade_result.get('entry_price', 0),
            'stop_loss_price': trade_result.get('stop_loss_price', 0),
            'take_profit_price': trade_result.get('take_profit_price', 0),
            'leverage': self.config.leverage,
            'entry_time': trade_result.get('entry_time', time.time())
        }

        self.context.update_position(position_info)

    def get_stats(self) -> Dict[str, Any]:
        """获取执行器统计信息"""
        total_trades = self.stats["total_trades"]
        winning_trades = self.stats["winning_trades"]
        losing_trades = self.stats["losing_trades"]

        win_rate = winning_trades / total_trades if total_trades > 0 else 0

        return {
            **self.stats,
            'win_rate': win_rate,
            'avg_pnl_per_trade': self.stats["total_pnl"] / total_trades if total_trades > 0 else 0
        }