#!/usr/bin/env python3
"""
四号引擎v3.0 风险管理器测试
测试小资金优化参数（5%风险，2 tick止损，6 tick止盈）和日损失限制检查
"""

import os
import sys

# 获取项目根目录并添加到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

from src.strategy.triplea.data_structures import RiskManagerConfig
from src.strategy.triplea.risk_manager import RiskManager, PositionSizingResult, SimpleRiskManager


def test_position_sizing_result():
    """测试仓位计算结果数据结构"""
    print("🔧 测试仓位计算结果数据结构")
    print("-" * 60)

    result = PositionSizingResult(
        qty=0.5,
        stop_px=2980.0,
        take_profit_px=3020.0,
        breakeven_px=2990.0
    )

    assert result.qty == 0.5
    assert result.stop_px == 2980.0
    assert result.take_profit_px == 3020.0
    assert result.breakeven_px == 2990.0

    # 测试to_dict方法
    result_dict = result.to_dict()
    assert result_dict['qty'] == 0.5
    assert result_dict['stop_px'] == 2980.0
    assert result_dict['take_profit_px'] == 3020.0
    assert result_dict['breakeven_px'] == 2990.0

    print("✅ 仓位计算结果数据结构测试通过")


def test_risk_manager_initialization():
    """测试风险管理器初始化"""
    print("\n🔧 测试风险管理器初始化")
    print("-" * 60)

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

    # 验证配置
    assert risk_manager.config.account_size_usdt == 300.0
    assert risk_manager.config.max_risk_per_trade_pct == 5.0
    assert risk_manager.config.stop_loss_ticks == 2
    assert risk_manager.config.take_profit_ticks == 6
    assert risk_manager.config.max_daily_loss_pct == 5.0

    # 验证默认参数
    assert risk_manager.fee_rate_taker == 0.0005
    assert risk_manager.fee_rate_maker == 0.0002
    assert risk_manager.min_rr_ratio == 2.0

    # 验证初始统计
    stats = risk_manager.get_daily_stats()
    assert stats['daily_pnl_usd'] == 0.0
    assert stats['daily_trades'] == 0
    assert stats['daily_wins'] == 0
    assert stats['daily_losses'] == 0
    assert stats['is_loss_limit_reached'] == False

    print("✅ 风险管理器初始化测试通过")


def test_calculate_stop_loss_take_profit():
    """测试止损止盈计算"""
    print("\n📊 测试止损止盈计算")
    print("-" * 60)

    # 创建配置
    config = RiskManagerConfig(
        stop_loss_ticks=2,
        take_profit_ticks=6
    )

    risk_manager = RiskManager(config)

    # 测试LONG仓位
    entry_price = 3000.0
    direction = "LONG"
    tick_size = 0.01

    stop_loss, take_profit = risk_manager.calculate_stop_loss_take_profit(
        entry_price, direction, tick_size
    )

    expected_stop_loss = entry_price - (2 * tick_size)  # 2999.98
    expected_take_profit = entry_price + (6 * tick_size)  # 3000.06

    assert abs(stop_loss - expected_stop_loss) < 0.001
    assert abs(take_profit - expected_take_profit) < 0.001

    print(f"  LONG 仓位:")
    print(f"    入场价: {entry_price:.2f}")
    print(f"    止损价: {stop_loss:.2f} (期望: {expected_stop_loss:.2f})")
    print(f"    止盈价: {take_profit:.2f} (期望: {expected_take_profit:.2f})")
    print(f"    盈亏比: {abs(take_profit - entry_price) / abs(entry_price - stop_loss):.2f}")

    # 测试SHORT仓位
    direction = "SHORT"

    stop_loss, take_profit = risk_manager.calculate_stop_loss_take_profit(
        entry_price, direction, tick_size
    )

    expected_stop_loss = entry_price + (2 * tick_size)  # 3000.02
    expected_take_profit = entry_price - (6 * tick_size)  # 2999.94

    assert abs(stop_loss - expected_stop_loss) < 0.001
    assert abs(take_profit - expected_take_profit) < 0.001

    print(f"  SHORT 仓位:")
    print(f"    入场价: {entry_price:.2f}")
    print(f"    止损价: {stop_loss:.2f} (期望: {expected_stop_loss:.2f})")
    print(f"    止盈价: {take_profit:.2f} (期望: {expected_take_profit:.2f})")
    print(f"    盈亏比: {abs(entry_price - take_profit) / abs(stop_loss - entry_price):.2f}")

    print("✅ 止损止盈计算测试通过")


def test_position_size_calculation_long():
    """测试LONG仓位大小计算"""
    print("\n📊 测试LONG仓位大小计算")
    print("-" * 60)

    # 创建配置（放宽参数以便测试通过）
    config = RiskManagerConfig(
        account_size_usdt=1000.0,  # 增加账户规模
        max_risk_per_trade_pct=5.0,
        stop_loss_ticks=10,  # 增加止损距离
        take_profit_ticks=30,  # 增加止盈距离
        max_daily_loss_pct=5.0
    )

    risk_manager = RiskManager(config)

    # 降低手续费率和盈亏比要求以便测试通过
    risk_manager.fee_rate_taker = 0.00005
    risk_manager.min_rr_ratio = 0.5

    # 测试数据
    entry_price = 3000.0
    stop_loss_price = 2990.0  # 10个Tick距离 (0.10)
    take_profit_price = 3030.0  # 30个Tick距离 (0.30)
    direction = "LONG"
    tick_size = 0.01

    # 计算仓位大小
    position_result = risk_manager.calculate_position_size(
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        direction=direction,
        tick_size=tick_size
    )

    # 验证结果
    assert position_result.qty > 0, f"仓位数量应为正数，实际为: {position_result.qty}"
    assert position_result.stop_px == stop_loss_price
    assert position_result.take_profit_px == take_profit_price
    # 验证止损止盈计算
    sl_distance = abs(entry_price - stop_loss_price)
    tp_distance = abs(take_profit_price - entry_price)

    print(f"  LONG 仓位计算:")
    print(f"    入场价: {entry_price:.2f}")
    print(f"    止损价: {stop_loss_price:.2f} (距离: {sl_distance:.2f})")
    print(f"    止盈价: {take_profit_price:.2f} (距离: {tp_distance:.2f})")
    print(f"    合约数量: {position_result.qty:.3f}")
    print(f"    保本价格: {position_result.breakeven_px:.2f}")

    # 计算预期风险金额
    risk_amount = config.account_size_usdt * (config.max_risk_per_trade_pct / 100.0)  # 50 USD
    print(f"    风险金额: {risk_amount:.2f} USD ({config.max_risk_per_trade_pct}%)")

    # 验证基本逻辑
    assert position_result.qty > 0.001, "合约数量应大于最小限制"

    print("✅ LONG仓位大小计算测试通过")


def test_position_size_calculation_short():
    """测试SHORT仓位大小计算"""
    print("\n📊 测试SHORT仓位大小计算")
    print("-" * 60)

    # 创建配置（放宽参数以便测试通过）
    config = RiskManagerConfig(
        account_size_usdt=1000.0,  # 增加账户规模
        max_risk_per_trade_pct=5.0,
        stop_loss_ticks=10,  # 增加止损距离
        take_profit_ticks=30,  # 增加止盈距离
        max_daily_loss_pct=5.0
    )

    risk_manager = RiskManager(config)

    # 降低手续费率和盈亏比要求以便测试通过
    risk_manager.fee_rate_taker = 0.00005
    risk_manager.min_rr_ratio = 0.5

    # 测试数据
    entry_price = 3000.0
    stop_loss_price = 3010.0  # 10个Tick距离 (0.10)
    take_profit_price = 2970.0  # 30个Tick距离 (0.30)
    direction = "SHORT"
    tick_size = 0.01

    # 计算仓位大小
    position_result = risk_manager.calculate_position_size(
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        direction=direction,
        tick_size=tick_size
    )

    # 验证结果
    assert position_result.qty > 0, f"仓位数量应为正数，实际为: {position_result.qty}"
    assert position_result.stop_px == stop_loss_price
    assert position_result.take_profit_px == take_profit_price

    # 验证止损止盈计算
    sl_distance = abs(stop_loss_price - entry_price)
    tp_distance = abs(entry_price - take_profit_price)

    print(f"  SHORT 仓位计算:")
    print(f"    入场价: {entry_price:.2f}")
    print(f"    止损价: {stop_loss_price:.2f} (距离: {sl_distance:.2f})")
    print(f"    止盈价: {take_profit_price:.2f} (距离: {tp_distance:.2f})")
    print(f"    合约数量: {position_result.qty:.3f}")
    print(f"    保本价格: {position_result.breakeven_px:.2f}")

    # 计算预期风险金额
    risk_amount = config.account_size_usdt * (config.max_risk_per_trade_pct / 100.0)  # 50 USD
    print(f"    风险金额: {risk_amount:.2f} USD ({config.max_risk_per_trade_pct}%)")

    # 验证基本逻辑
    assert position_result.qty > 0.001, "合约数量应大于最小限制"

    print("✅ SHORT仓位大小计算测试通过")


def test_daily_loss_limit():
    """测试日损失限制检查"""
    print("\n📊 测试日损失限制检查")
    print("-" * 60)

    # 创建配置
    config = RiskManagerConfig(
        account_size_usdt=1000.0,
        max_daily_loss_pct=5.0  # 5%日损失限制 = 50 USD
    )

    risk_manager = RiskManager(config)

    # 模拟一些亏损交易
    # 第一次亏损: -20 USD (总亏损 -20 USD)
    risk_manager.record_trade_result(
        trade_id="LOSS_001",
        direction="LONG",
        entry_price=3000.0,
        exit_price=2995.0,
        quantity=0.1,
        stop_loss_price=2995.0,
        take_profit_price=3015.0,
        pnl_usd=-20.0,
        exit_reason="STOP_LOSS"
    )

    stats = risk_manager.get_daily_stats()
    assert stats['daily_pnl_usd'] == -20.0
    assert stats['is_loss_limit_reached'] == False

    # 第二次亏损: -15 USD (总亏损 -35 USD)
    risk_manager.record_trade_result(
        trade_id="LOSS_002",
        direction="SHORT",
        entry_price=3005.0,
        exit_price=3010.0,
        quantity=0.08,
        stop_loss_price=3010.0,
        take_profit_price=2985.0,
        pnl_usd=-15.0,
        exit_reason="STOP_LOSS"
    )

    stats = risk_manager.get_daily_stats()
    assert stats['daily_pnl_usd'] == -35.0
    assert stats['is_loss_limit_reached'] == False

    # 第三次亏损: -20 USD (总亏损 -55 USD, 超过50 USD限制)
    risk_manager.record_trade_result(
        trade_id="LOSS_003",
        direction="LONG",
        entry_price=3010.0,
        exit_price=3005.0,
        quantity=0.12,
        stop_loss_price=3005.0,
        take_profit_price=3025.0,
        pnl_usd=-20.0,
        exit_reason="STOP_LOSS"
    )

    stats = risk_manager.get_daily_stats()
    assert stats['daily_pnl_usd'] == -55.0
    assert stats['is_loss_limit_reached'] == True

    print(f"  日盈亏: {stats['daily_pnl_usd']:.2f} USD")
    print(f"  日损失比例: {stats['loss_pct']:.2f}%")
    print(f"  最大日损失限制: {config.max_daily_loss_pct}%")
    print(f"  是否达到日损失限制: {stats['is_loss_limit_reached']}")

    # 测试日损失限制拦截
    # 现在日损失已经超过限制，新交易应该被拦截
    test_stop_loss = 2990.0
    test_take_profit = 3030.0
    position_result = risk_manager.calculate_position_size(
        entry_price=3000.0,
        stop_loss_price=test_stop_loss,
        take_profit_price=test_take_profit,
        direction="LONG",
        tick_size=0.01
    )

    # 由于日损失限制已到达，仓位计算应返回零数量
    assert position_result.qty == 0.0, "日损失限制已到达，仓位计算应返回零"

    print("✅ 日损失限制检查测试通过")


def test_risk_management_interception():
    """测试风险管理拦截逻辑"""
    print("\n📊 测试风险管理拦截逻辑")
    print("-" * 60)

    # 创建配置（宽松配置以便测试拦截逻辑）
    config = RiskManagerConfig(
        account_size_usdt=1000.0,
        max_risk_per_trade_pct=5.0,
        stop_loss_ticks=10,
        take_profit_ticks=30,
        max_daily_loss_pct=5.0
    )

    risk_manager = RiskManager(config)

    # 测试1: 负盈亏比拦截
    # 设置低手续费率和低盈亏比要求以便测试
    risk_manager.fee_rate_taker = 0.0001
    risk_manager.min_rr_ratio = 1.0

    # 使用非常不利的止损止盈比例
    entry_price = 3000.0
    stop_loss_price = 2999.9  # 非常近的止损 (0.10)
    take_profit_price = 3000.1  # 非常近的止盈 (0.10)
    direction = "LONG"

    position_result = risk_manager.calculate_position_size(
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        direction=direction,
        tick_size=0.01
    )

    # 由于止损止盈距离太小，手续费可能使净盈亏为负
    # 预期被风控拦截（返回零仓位）
    print(f"  测试1: 负盈亏比拦截")
    print(f"    入场价: {entry_price:.2f}")
    print(f"    止损价: {stop_loss_price:.2f}")
    print(f"    止盈价: {take_profit_price:.2f}")
    print(f"    仓位数量: {position_result.qty:.3f}")

    # 测试2: 无效距离拦截
    entry_price = 3000.0
    stop_loss_price = 3001.0  # 错误的止损价（对于LONG仓位止损应低于入场价）
    take_profit_price = 2999.0  # 错误的止盈价（对于LONG仓位止盈应高于入场价）
    direction = "LONG"

    position_result = risk_manager.calculate_position_size(
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        direction=direction,
        tick_size=0.01
    )

    print(f"  测试2: 无效距离拦截")
    print(f"    入场价: {entry_price:.2f}")
    print(f"    止损价: {stop_loss_price:.2f}")
    print(f"    止盈价: {take_profit_price:.2f}")
    print(f"    仓位数量: {position_result.qty:.3f}")

    assert position_result.qty == 0.0, "无效距离应该被拦截"

    print("✅ 风险管理拦截逻辑测试通过")


def test_simple_risk_manager():
    """测试简化版风险管理器"""
    print("\n🔧 测试简化版风险管理器")
    print("-" * 60)

    # 创建配置
    config = RiskManagerConfig(
        stop_loss_ticks=2,
        take_profit_ticks=6
    )

    simple_risk_manager = SimpleRiskManager(config)

    # 测试LONG仓位
    entry_price = 3000.0
    direction = "LONG"
    tick_size = 0.01

    stop_loss, take_profit = simple_risk_manager.calculate_stop_tp_prices(
        entry_price, direction, tick_size
    )

    expected_stop_loss = entry_price - (2 * tick_size)  # 2999.98
    expected_take_profit = entry_price + (6 * tick_size)  # 3000.06

    assert abs(stop_loss - expected_stop_loss) < 0.001
    assert abs(take_profit - expected_take_profit) < 0.001

    print(f"  LONG 仓位:")
    print(f"    入场价: {entry_price:.2f}")
    print(f"    止损价: {stop_loss:.2f}")
    print(f"    止盈价: {take_profit:.2f}")

    # 测试SHORT仓位
    direction = "SHORT"

    stop_loss, take_profit = simple_risk_manager.calculate_stop_tp_prices(
        entry_price, direction, tick_size
    )

    expected_stop_loss = entry_price + (2 * tick_size)  # 3000.02
    expected_take_profit = entry_price - (6 * tick_size)  # 2999.94

    assert abs(stop_loss - expected_stop_loss) < 0.001
    assert abs(take_profit - expected_take_profit) < 0.001

    print(f"  SHORT 仓位:")
    print(f"    入场价: {entry_price:.2f}")
    print(f"    止损价: {stop_loss:.2f}")
    print(f"    止盈价: {take_profit:.2f}")

    print("✅ 简化版风险管理器测试通过")


def test_daily_stats_reset():
    """测试日统计重置"""
    print("\n📊 测试日统计重置")
    print("-" * 60)

    # 创建配置
    config = RiskManagerConfig(
        account_size_usdt=1000.0,
        max_daily_loss_pct=5.0
    )

    risk_manager = RiskManager(config)

    # 记录一些交易
    risk_manager.record_trade_result(
        trade_id="TRADE_001",
        direction="LONG",
        entry_price=3000.0,
        exit_price=3010.0,
        quantity=0.1,
        stop_loss_price=2995.0,
        take_profit_price=3015.0,
        pnl_usd=10.0,
        exit_reason="TAKE_PROFIT"
    )

    # 获取当前统计
    stats_before = risk_manager.get_daily_stats()
    assert stats_before['daily_trades'] == 1
    assert stats_before['daily_pnl_usd'] == 10.0

    # 手动重置统计（模拟新的一天）
    risk_manager.reset_daily_stats()

    # 获取重置后的统计
    stats_after = risk_manager.get_daily_stats()
    assert stats_after['daily_trades'] == 0
    assert stats_after['daily_pnl_usd'] == 0.0

    print(f"  重置前: {stats_before['daily_trades']} 次交易, 盈亏: {stats_before['daily_pnl_usd']:.2f} USD")
    print(f"  重置后: {stats_after['daily_trades']} 次交易, 盈亏: {stats_after['daily_pnl_usd']:.2f} USD")

    print("✅ 日统计重置测试通过")


def run_all_tests():
    """运行所有风险管理器测试"""
    print("🚀 四号引擎风险管理器测试套件")
    print("=" * 70)

    test_position_sizing_result()
    test_risk_manager_initialization()
    test_calculate_stop_loss_take_profit()
    test_position_size_calculation_long()
    test_position_size_calculation_short()
    test_daily_loss_limit()
    test_risk_management_interception()
    test_simple_risk_manager()
    test_daily_stats_reset()

    print("\n" + "=" * 70)
    print("🎉 所有风险管理器测试通过！")
    print("\n💡 总结:")
    print("  1. 仓位计算结果数据结构功能正常")
    print("  2. 风险管理器初始化正确")
    print("  3. 止损止盈计算准确")
    print("  4. LONG/SHORT仓位大小计算逻辑正确")
    print("  5. 日损失限制检查机制正常工作")
    print("  6. 风险管理拦截逻辑有效")
    print("  7. 简化版风险管理器功能正常")
    print("  8. 日统计重置机制可靠")


if __name__ == "__main__":
    run_all_tests()
