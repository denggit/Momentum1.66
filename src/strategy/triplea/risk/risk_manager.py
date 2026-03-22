#!/usr/bin/env python3
"""
四号引擎v3.0 风险管理器
小资金优化版：5%单笔风险，2 tick止损，6 tick止盈，5%日损失限制
"""

from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional, Dict, Any

from src.strategy.triplea.core.data_structures import RiskManagerConfig


@dataclass
class PositionSizingResult:
    """仓位计算结果"""
    qty: float  # 合约数量
    stop_px: float  # 止损价格（物理锚点）
    take_profit_px: float  # 止盈价格
    breakeven_px: float  # 保本价格

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'qty': self.qty,
            'stop_px': self.stop_px,
            'take_profit_px': self.take_profit_px,
            'breakeven_px': self.breakeven_px
        }


class RiskManager:
    """风险管理器（小资金优化版）

    功能：
    1. 动态仓位计算（5%风险模型）
    2. 止损止盈计算（2 tick止损，6 tick止盈）
    3. 日损失限制检查（5%）
    4. 每日盈亏跟踪
    """

    def __init__(self, config: RiskManagerConfig):
        """初始化风险管理器

        Args:
            config: 风控配置
        """
        self.config = config

        # 每日状态跟踪
        self.daily_pnl: float = 0.0  # 当日盈亏（USD）
        self.daily_trades: int = 0  # 当日交易次数
        self.daily_losses: int = 0  # 当日亏损次数
        self.daily_wins: int = 0  # 当日盈利次数
        self.last_trade_date: Optional[date] = None  # 最后交易日期

        # 交易记录
        self.trade_history: list = []  # 交易历史记录

        # 默认手续费率（OKX ETH永续合约）
        self.fee_rate_taker: float = 0.0005  # 0.05% 吃单手续费
        self.fee_rate_maker: float = 0.0002  # 0.02% 挂单手续费

        # 最小盈亏比要求
        self.min_rr_ratio: float = 2.0  # 最小净盈亏比要求（根据用户反馈）

        # 初始化日状态
        self._reset_daily_if_needed()

    def _reset_daily_if_needed(self):
        """检查是否需要重置日状态"""
        today = date.today()

        if self.last_trade_date is None or self.last_trade_date != today:
            # 新的一天，重置日状态
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.daily_losses = 0
            self.daily_wins = 0
            self.last_trade_date = today

            print(f"📅 风险管理器：新的一天 ({today})，日状态已重置")

    def calculate_position_size_with_structure(
            self,
            entry_price: float,
            structure_sl_price: float,  # 结构性止损价格（吸收点下方2ticks）
            structure_tp_price: float,  # 结构性止盈价格（VAH/VAL附近）
            direction: str,
            tick_size: float = 0.01
    ) -> PositionSizingResult:
        """基于结构性止损止盈计算仓位大小

        Args:
            entry_price: 入场价格
            structure_sl_price: 结构性止损价格（吸收点下方2ticks）
            structure_tp_price: 结构性止盈价格（VAH/VAL附近）
            direction: 交易方向 ("LONG" 或 "SHORT")
            tick_size: 最小价格变动单位

        Returns:
            PositionSizingResult: 仓位计算结果
        """
        # 确保日状态是最新的
        self._reset_daily_if_needed()

        # 1. 检查日损失限制
        if self._is_daily_loss_limit_reached():
            print(f"⚠️ 风控拦截：日损失已达到限制 ({self.config.max_daily_loss_pct}%)")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 2. 验证结构位价格合理性
        if direction == "LONG":
            # 对于LONG：止损应低于入场价，止盈应高于入场价
            if structure_sl_price >= entry_price:
                print(f"⚠️ 风控拦截：LONG仓位止损价 {structure_sl_price:.2f} 应低于入场价 {entry_price:.2f}")
                return PositionSizingResult(0.0, 0.0, 0.0, 0.0)
            if structure_tp_price <= entry_price:
                print(f"⚠️ 风控拦截：LONG仓位止盈价 {structure_tp_price:.2f} 应高于入场价 {entry_price:.2f}")
                return PositionSizingResult(0.0, 0.0, 0.0, 0.0)
        else:  # SHORT
            # 对于SHORT：止损应高于入场价，止盈应低于入场价
            if structure_sl_price <= entry_price:
                print(f"⚠️ 风控拦截：SHORT仓位止损价 {structure_sl_price:.2f} 应高于入场价 {entry_price:.2f}")
                return PositionSizingResult(0.0, 0.0, 0.0, 0.0)
            if structure_tp_price >= entry_price:
                print(f"⚠️ 风控拦截：SHORT仓位止盈价 {structure_tp_price:.2f} 应低于入场价 {entry_price:.2f}")
                return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 3. 计算止损和止盈距离
        if direction == "LONG":
            sl_distance = entry_price - structure_sl_price  # 止损距离（正数）
            tp_distance = structure_tp_price - entry_price  # 止盈距离（正数）
        else:  # SHORT
            sl_distance = structure_sl_price - entry_price  # 止损距离（正数）
            tp_distance = entry_price - structure_tp_price  # 止盈距离（正数）

        # 确保距离为正数
        if sl_distance <= 0 or tp_distance <= 0:
            print(f"⚠️ 风控拦截：无效的止损/止盈距离 (SL={sl_distance}, TP={tp_distance})")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 检查最小止盈距离（至少0.2%）
        min_tp_distance_pct = 0.002  # 0.2%
        min_tp_distance = entry_price * min_tp_distance_pct

        if tp_distance < min_tp_distance:
            print(
                f"⚠️ 风控拦截：止盈距离过小 ({tp_distance:.2f} < {min_tp_distance:.2f}, 需要至少{min_tp_distance_pct * 100:.1f}%)")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 4. 计算风险金额（5%风险模型）
        risk_amount = self.config.account_size_usdt * (self.config.max_risk_per_trade_pct / 100.0)

        # 5. 预估手续费（双边吃单手续费）
        estimated_fee_per_contract = entry_price * self.fee_rate_taker * 2

        # 6. 计算合约数量
        # 公式：风险金额 = 合约数量 × (止损距离 + 预估手续费)
        qty = risk_amount / (sl_distance + estimated_fee_per_contract)

        # 四舍五入到合适精度（ETH合约通常支持3位小数）
        qty = round(qty, 3)

        # 7. 检查最小合约数量
        if qty < 0.001:  # 最小合约数量限制
            print(f"⚠️ 风控拦截：合约数量过小 ({qty:.4f})")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 8. 计算预期盈亏并验证盈亏比
        expected_gross_profit = qty * tp_distance
        total_estimated_fee = qty * estimated_fee_per_contract
        expected_net_profit = expected_gross_profit - total_estimated_fee

        # 检查净盈亏是否为负
        if expected_net_profit <= 0:
            print(f"⚠️ 风控拦截：预期净盈亏为负 ({expected_net_profit:.2f} USD)")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 计算净盈亏比
        net_rr_ratio = expected_net_profit / risk_amount

        if net_rr_ratio < self.min_rr_ratio:
            print(f"⚠️ 风控拦截：净盈亏比不足 ({net_rr_ratio:.2f} < {self.min_rr_ratio})")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 9. 计算保本价格（入场价 + 单边手续费 + 1个Tick缓冲）
        breakeven_offset = (entry_price * self.fee_rate_taker) + tick_size
        if direction == "LONG":
            breakeven_px = entry_price + breakeven_offset
        else:  # SHORT
            breakeven_px = entry_price - breakeven_offset

        print(f"✅ 结构性仓位计算完成：")
        print(f"   方向: {direction}")
        print(f"   入场价: {entry_price:.2f}")
        print(f"   结构性止损价: {structure_sl_price:.2f} (距离: {sl_distance:.2f})")
        print(f"   结构性止盈价: {structure_tp_price:.2f} (距离: {tp_distance:.2f})")
        print(f"   合约数量: {qty:.3f}")
        print(f"   风险金额: {risk_amount:.2f} USD ({self.config.max_risk_per_trade_pct}%)")
        print(f"   预期净盈利: {expected_net_profit:.2f} USD (净盈亏比: {net_rr_ratio:.2f})")
        print(f"   保本价格: {breakeven_px:.2f}")

        return PositionSizingResult(qty, structure_sl_price, structure_tp_price, breakeven_px)

    def calculate_position_size(
            self,
            entry_price: float,
            stop_loss_price: float,
            take_profit_price: float,
            direction: str,
            tick_size: float = 0.01
    ) -> PositionSizingResult:
        """计算基于风险的仓位大小

        基于5%风险模型计算仓位大小：
        1. 计算止损距离（以USD计）
        2. 计算风险金额（账户规模 * 5%）
        3. 计算合约数量：风险金额 / (止损距离 + 预估手续费)
        4. 验证净盈亏比是否达标（> 1.5）

        Args:
            entry_price: 入场价格
            stop_loss_price: 止损价格
            take_profit_price: 止盈价格
            direction: 交易方向 ("LONG" 或 "SHORT")
            tick_size: 最小价格变动单位

        Returns:
            PositionSizingResult: 仓位计算结果，如果被风控拦截则返回零仓位
        """
        # 确保日状态是最新的
        self._reset_daily_if_needed()

        # 1. 检查日损失限制
        if self._is_daily_loss_limit_reached():
            print(f"⚠️ 风控拦截：日损失已达到限制 ({self.config.max_daily_loss_pct}%)")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 2. 计算止损和止盈距离
        if direction == "LONG":
            sl_distance = entry_price - stop_loss_price  # 止损距离（正数）
            tp_distance = take_profit_price - entry_price  # 止盈距离（正数）
        else:  # SHORT
            sl_distance = stop_loss_price - entry_price  # 止损距离（正数）
            tp_distance = entry_price - take_profit_price  # 止盈距离（正数）

        # 确保距离为正数
        if sl_distance <= 0 or tp_distance <= 0:
            print(f"⚠️ 风控拦截：无效的止损/止盈距离 (SL={sl_distance}, TP={tp_distance})")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 3. 计算风险金额（5%风险模型）
        risk_amount = self.config.account_size_usdt * (self.config.max_risk_per_trade_pct / 100.0)

        # 4. 预估手续费（双边吃单手续费）
        estimated_fee_per_contract = entry_price * self.fee_rate_taker * 2

        # 5. 计算合约数量
        # 公式：风险金额 = 合约数量 × (止损距离 + 预估手续费)
        qty = risk_amount / (sl_distance + estimated_fee_per_contract)

        # 四舍五入到合适精度（ETH合约通常支持3位小数）
        qty = round(qty, 3)

        # 6. 检查最小合约数量
        if qty < 0.001:  # 最小合约数量限制
            print(f"⚠️ 风控拦截：合约数量过小 ({qty:.4f})")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 7. 计算预期盈亏并验证盈亏比
        expected_gross_profit = qty * tp_distance
        total_estimated_fee = qty * estimated_fee_per_contract
        expected_net_profit = expected_gross_profit - total_estimated_fee

        # 检查净盈亏是否为负
        if expected_net_profit <= 0:
            print(f"⚠️ 风控拦截：预期净盈亏为负 ({expected_net_profit:.2f} USD)")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 计算净盈亏比
        net_rr_ratio = expected_net_profit / risk_amount

        if net_rr_ratio < self.min_rr_ratio:
            print(f"⚠️ 风控拦截：净盈亏比不足 ({net_rr_ratio:.2f} < {self.min_rr_ratio})")
            return PositionSizingResult(0.0, 0.0, 0.0, 0.0)

        # 8. 计算保本价格（入场价 + 单边手续费 + 1个Tick缓冲）
        breakeven_offset = (entry_price * self.fee_rate_taker) + tick_size
        if direction == "LONG":
            breakeven_px = entry_price + breakeven_offset
        else:  # SHORT
            breakeven_px = entry_price - breakeven_offset

        print(f"✅ 仓位计算完成：")
        print(f"   方向: {direction}")
        print(f"   入场价: {entry_price:.2f}")
        print(f"   止损价: {stop_loss_price:.2f} (距离: {sl_distance:.2f})")
        print(f"   止盈价: {take_profit_price:.2f} (距离: {tp_distance:.2f})")
        print(f"   合约数量: {qty:.3f}")
        print(f"   风险金额: {risk_amount:.2f} USD ({self.config.max_risk_per_trade_pct}%)")
        print(f"   预期净盈利: {expected_net_profit:.2f} USD (净盈亏比: {net_rr_ratio:.2f})")
        print(f"   保本价格: {breakeven_px:.2f}")

        return PositionSizingResult(qty, stop_loss_price, take_profit_price, breakeven_px)

    def calculate_stop_loss_take_profit(
            self,
            entry_price: float,
            direction: str,
            tick_size: float = 0.01
    ) -> tuple[float, float]:
        """计算标准的止损止盈价格（基于配置的Tick数）

        Args:
            entry_price: 入场价格
            direction: 交易方向 ("LONG" 或 "SHORT")
            tick_size: 最小价格变动单位

        Returns:
            tuple: (stop_loss_price, take_profit_price)
        """
        # 从配置获取Tick数
        stop_loss_ticks = self.config.stop_loss_ticks  # 2个Tick
        take_profit_ticks = self.config.take_profit_ticks  # 6个Tick

        if direction == "LONG":
            stop_loss_price = entry_price - (stop_loss_ticks * tick_size)
            take_profit_price = entry_price + (take_profit_ticks * tick_size)
        else:  # SHORT
            stop_loss_price = entry_price + (stop_loss_ticks * tick_size)
            take_profit_price = entry_price - (take_profit_ticks * tick_size)

        return stop_loss_price, take_profit_price

    def _is_daily_loss_limit_reached(self) -> bool:
        """检查是否达到日损失限制

        Returns:
            bool: 如果达到日损失限制则返回True
        """
        # 如果日盈亏为负且绝对值超过日损失限制
        if self.daily_pnl < 0:
            loss_pct = abs(self.daily_pnl) / self.config.account_size_usdt * 100.0
            if loss_pct >= self.config.max_daily_loss_pct:
                return True

        return False

    def record_trade_result(
            self,
            trade_id: str,
            direction: str,
            entry_price: float,
            exit_price: float,
            quantity: float,
            stop_loss_price: float,
            take_profit_price: float,
            pnl_usd: float,
            exit_reason: str
    ):
        """记录交易结果并更新日统计

        Args:
            trade_id: 交易ID
            direction: 交易方向
            entry_price: 入场价格
            exit_price: 出场价格
            quantity: 合约数量
            stop_loss_price: 止损价格
            take_profit_price: 止盈价格
            pnl_usd: 盈亏金额（USD）
            exit_reason: 出场原因（"TAKE_PROFIT", "STOP_LOSS", "MANUAL"等）
        """
        # 确保日状态是最新的
        self._reset_daily_if_needed()

        # 更新日统计
        self.daily_pnl += pnl_usd
        self.daily_trades += 1

        if pnl_usd > 0:
            self.daily_wins += 1
        else:
            self.daily_losses += 1

        # 记录交易历史
        trade_record = {
            'trade_id': trade_id,
            'timestamp': datetime.now().isoformat(),
            'direction': direction,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'quantity': quantity,
            'stop_loss_price': stop_loss_price,
            'take_profit_price': take_profit_price,
            'pnl_usd': pnl_usd,
            'exit_reason': exit_reason,
            'daily_pnl_after': self.daily_pnl
        }

        self.trade_history.append(trade_record)

        # 打印交易结果摘要
        win_loss = "盈利" if pnl_usd > 0 else "亏损"
        print(f"📊 交易记录 {trade_id}: {win_loss} {abs(pnl_usd):.2f} USD ({exit_reason})")
        print(f"   日累计盈亏: {self.daily_pnl:.2f} USD ({self.daily_wins}胜/{self.daily_losses}负)")

        # 检查日损失限制
        if self._is_daily_loss_limit_reached():
            print(f"🚨 警告：已达到日损失限制 ({self.config.max_daily_loss_pct}%)")

    def get_daily_stats(self) -> Dict[str, Any]:
        """获取日统计信息

        Returns:
            Dict: 日统计信息
        """
        win_rate = 0.0
        if self.daily_trades > 0:
            win_rate = self.daily_wins / self.daily_trades * 100.0

        loss_pct = 0.0
        if self.config.account_size_usdt > 0:
            loss_pct = abs(min(self.daily_pnl, 0)) / self.config.account_size_usdt * 100.0

        return {
            'date': self.last_trade_date.isoformat() if self.last_trade_date else None,
            'daily_pnl_usd': self.daily_pnl,
            'daily_trades': self.daily_trades,
            'daily_wins': self.daily_wins,
            'daily_losses': self.daily_losses,
            'win_rate_pct': win_rate,
            'loss_pct': loss_pct,
            'max_daily_loss_pct': self.config.max_daily_loss_pct,
            'is_loss_limit_reached': self._is_daily_loss_limit_reached()
        }

    def get_statistics(self) -> Dict[str, Any]:
        """获取风险管理器统计信息

        Returns:
            Dict: 统计信息
        """
        daily_stats = self.get_daily_stats()

        total_trades = len(self.trade_history)
        total_pnl = sum(trade['pnl_usd'] for trade in self.trade_history)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0

        # 计算胜率
        total_wins = sum(1 for trade in self.trade_history if trade['pnl_usd'] > 0)
        total_losses = total_trades - total_wins
        overall_win_rate = total_wins / total_trades * 100.0 if total_trades > 0 else 0.0

        return {
            **daily_stats,
            'total_trades': total_trades,
            'total_pnl_usd': total_pnl,
            'average_pnl_usd': avg_pnl,
            'total_wins': total_wins,
            'total_losses': total_losses,
            'overall_win_rate_pct': overall_win_rate,
            'account_size_usdt': self.config.account_size_usdt,
            'max_risk_per_trade_pct': self.config.max_risk_per_trade_pct,
            'stop_loss_ticks': self.config.stop_loss_ticks,
            'take_profit_ticks': self.config.take_profit_ticks,
            'max_daily_loss_pct': self.config.max_daily_loss_pct
        }

    def reset_daily_stats(self):
        """重置日统计（用于测试或新的一天）"""
        today = date.today()
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_losses = 0
        self.daily_wins = 0
        self.last_trade_date = today

        print(f"🔄 日统计已重置 ({today})")


# 简化版风险管理器（用于状态机集成）
class SimpleRiskManager:
    """简化版风险管理器（仅用于状态机集成）

    提供基本的止损止盈计算功能，不包含复杂的仓位计算和日限制检查
    """

    def __init__(self, config: RiskManagerConfig):
        self.config = config

    def calculate_stop_tp_prices(
            self,
            entry_price: float,
            direction: str,
            tick_size: float = 0.01
    ) -> tuple[float, float]:
        """计算止损止盈价格

        Args:
            entry_price: 入场价格
            direction: 交易方向 ("LONG" 或 "SHORT")
            tick_size: 最小价格变动单位

        Returns:
            tuple: (stop_loss_price, take_profit_price)
        """
        stop_loss_ticks = self.config.stop_loss_ticks
        take_profit_ticks = self.config.take_profit_ticks

        if direction == "LONG":
            stop_loss = entry_price - (stop_loss_ticks * tick_size)
            take_profit = entry_price + (take_profit_ticks * tick_size)
        else:  # SHORT
            stop_loss = entry_price + (stop_loss_ticks * tick_size)
            take_profit = entry_price - (take_profit_ticks * tick_size)

        return stop_loss, take_profit


if __name__ == "__main__":
    """风险管理器测试"""
    print("🧪 风险管理器测试")
    print("=" * 60)

    # 创建配置
    config = RiskManagerConfig(
        account_size_usdt=300.0,
        max_risk_per_trade_pct=5.0,
        stop_loss_ticks=2,
        take_profit_ticks=6,
        max_daily_loss_pct=5.0
    )

    # 创建风险管理器
    risk_manager = RiskManager(config)

    # 测试1：计算止损止盈价格
    print("\n📊 测试1：止损止盈计算")
    entry_price = 3000.0
    direction = "LONG"

    stop_loss, take_profit = risk_manager.calculate_stop_loss_take_profit(
        entry_price, direction
    )

    print(f"  入场价: {entry_price:.2f}")
    print(f"  方向: {direction}")
    print(f"  止损价: {stop_loss:.2f} ({config.stop_loss_ticks} ticks)")
    print(f"  止盈价: {take_profit:.2f} ({config.take_profit_ticks} ticks)")
    print(f"  盈亏比: {abs(take_profit - entry_price) / abs(entry_price - stop_loss):.2f}")

    # 测试2：计算仓位大小
    print("\n📊 测试2：仓位计算")
    position_result = risk_manager.calculate_position_size(
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        direction=direction
    )

    if position_result.qty > 0:
        print(f"✅ 仓位计算成功:")
        print(f"  合约数量: {position_result.qty:.3f}")
        print(f"  止损价: {position_result.stop_px:.2f}")
        print(f"  止盈价: {position_result.take_profit_px:.2f}")
        print(f"  保本价: {position_result.breakeven_px:.2f}")
    else:
        print("❌ 仓位计算被风控拦截")

    # 测试3：日损失限制检查
    print("\n📊 测试3：日损失限制检查")

    # 模拟一些亏损交易
    risk_manager.record_trade_result(
        trade_id="TEST_001",
        direction="LONG",
        entry_price=3000.0,
        exit_price=2998.0,
        quantity=0.1,
        stop_loss_price=2998.0,
        take_profit_price=3006.0,
        pnl_usd=-20.0,
        exit_reason="STOP_LOSS"
    )

    stats = risk_manager.get_daily_stats()
    print(f"  日盈亏: {stats['daily_pnl_usd']:.2f} USD")
    print(f"  日损失比例: {stats['loss_pct']:.2f}%")
    print(f"  是否达到日损失限制: {stats['is_loss_limit_reached']}")

    # 测试4：获取完整统计
    print("\n📊 测试4：完整统计信息")
    full_stats = risk_manager.get_statistics()
    for key, value in full_stats.items():
        print(f"  {key}: {value}")

    print("\n✅ 风险管理器测试完成")
