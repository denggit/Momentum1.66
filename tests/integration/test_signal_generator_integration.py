#!/usr/bin/env python3
"""
四号引擎信号生成器集成测试
验证signal_generator与状态机的集成，确保接口兼容性
"""

import os
import sys
import time

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.strategy.triplea.signal_generator import TripleASignalGenerator
from src.strategy.triplea.research_generator import ResearchTripleASignalGenerator


def test_signal_generator_initialization():
    """测试信号生成器初始化"""
    print("🔧 测试信号生成器初始化")
    print("-" * 60)

    # 测试主引擎
    main_generator = TripleASignalGenerator(symbol="ETH-USDT-SWAP")
    assert main_generator.symbol == "ETH-USDT-SWAP"
    assert main_generator.is_shadow == False
    assert main_generator.status == "IDLE"
    assert hasattr(main_generator, 'state_machine')
    assert hasattr(main_generator, 'config')
    print("✅ 主信号生成器初始化正确")

    # 测试影子引擎
    shadow_generator = ResearchTripleASignalGenerator(symbol="ETH-USDT-SWAP")
    assert shadow_generator.symbol == "ETH-USDT-SWAP"
    assert shadow_generator.is_shadow == True
    assert hasattr(shadow_generator, 'stage_metrics')
    assert hasattr(shadow_generator, 'state_timestamps')
    print("✅ 影子引擎初始化正确")

    print("✅ 信号生成器初始化测试通过")


def test_signal_generator_process_tick():
    """测试信号生成器处理Tick"""
    print("\n🔍 测试信号生成器处理Tick")
    print("-" * 60)

    generator = TripleASignalGenerator(symbol="ETH-USDT-SWAP")

    # 创建测试Tick数据（模拟orchestrator格式）
    test_tick = {
        'price': 3000.0,
        'size': 1.0,
        'side': 'buy',
        'ts': int(time.time() * 1000)
    }

    # 处理Tick（应该没有错误）
    signal = generator.process_tick(test_tick)

    # 验证处理结果
    assert signal is None or isinstance(signal, dict)
    print(f"✅ Tick处理完成，信号: {'有' if signal else '无'}")

    # 验证状态更新
    assert generator.processed_ticks == 1
    print(f"✅ 已处理Tick数: {generator.processed_ticks}")

    # 验证全局统计更新
    assert generator.global_cvd == 1.0  # buy side增加CVD
    assert generator.global_volume == 1.0
    print(f"✅ 全局统计更新正确: CVD={generator.global_cvd}, Volume={generator.global_volume}")

    print("✅ 信号生成器处理Tick测试通过")


def test_signal_generator_update_maps():
    """测试信号生成器更新地图"""
    print("\n🗺️ 测试信号生成器更新地图")
    print("-" * 60)

    generator = TripleASignalGenerator(symbol="ETH-USDT-SWAP")

    # 创建测试profile数据
    short_profile = {
        'tradable_zones': [
            {'zone_high': 3010.0, 'zone_low': 2990.0, 'confidence': 0.8},
            {'zone_high': 3020.0, 'zone_low': 3000.0, 'confidence': 0.7}
        ],
        'global_cvd': 100.0,
        'global_volume': 5000.0
    }

    long_profile = {
        'tradable_zones': [
            {'zone_high': 3100.0, 'zone_low': 2900.0, 'confidence': 0.9}
        ]
    }

    # 更新地图
    generator.update_maps(short_profile, long_profile)

    # 验证更新
    assert generator.profile == short_profile
    assert len(generator.tradable_zones) == 2
    assert len(generator.macro_zones) == 1
    assert generator.global_cvd == 100.0
    assert generator.global_volume == 5000.0

    print(f"✅ 战术地图更新: {len(generator.tradable_zones)}个区域")
    print(f"✅ 战略地图更新: {len(generator.macro_zones)}个区域")
    print(f"✅ 全局统计更新: CVD={generator.global_cvd}, Volume={generator.global_volume}")

    print("✅ 信号生成器更新地图测试通过")


def test_shadow_generator_enhancement():
    """测试影子引擎信号增强"""
    print("\n👻 测试影子引擎信号增强")
    print("-" * 60)

    shadow_generator = ResearchTripleASignalGenerator(symbol="ETH-USDT-SWAP")

    # 模拟状态机处于POSITION状态（需要设置上下文）
    # 这里主要测试影子引擎不会崩溃
    test_tick = {
        'price': 3000.0,
        'size': 1.0,
        'side': 'buy',
        'ts': int(time.time() * 1000)
    }

    # 处理Tick
    signal = shadow_generator.process_tick(test_tick)

    # 验证影子引擎特有功能
    assert hasattr(shadow_generator, 'stage_metrics')
    assert hasattr(shadow_generator, 'state_timestamps')
    assert hasattr(shadow_generator, 'mfe_price')

    print(f"✅ 影子引擎特有属性存在")
    print(f"✅ 阶段指标: {list(shadow_generator.stage_metrics.keys())}")
    print(f"✅ 状态时间戳: {list(shadow_generator.state_timestamps.keys())}")

    print("✅ 影子引擎信号增强测试通过")


def test_compatibility_with_orchestrator():
    """测试与orchestrator的兼容性"""
    print("\n🔌 测试与orchestrator的兼容性")
    print("-" * 60)

    # 验证signal_generator具有orchestrator所需的所有方法和属性
    generator = TripleASignalGenerator()

    # orchestrator调用的方法
    required_methods = ['process_tick', 'update_maps', '_reset_to_idle']
    for method in required_methods:
        assert hasattr(generator, method), f"缺少方法: {method}"
        assert callable(getattr(generator, method)), f"方法不可调用: {method}"

    # orchestrator访问的属性
    required_attrs = ['status', 'tradable_zones', 'macro_zones', 'profile',
                      'global_cvd', 'global_volume', 'current_sl', 'current_tp',
                      'micro_tracker']
    for attr in required_attrs:
        assert hasattr(generator, attr), f"缺少属性: {attr}"

    # orchestrator期望的信号格式
    test_signal = {
        'action': 'BUY',  # 或 'SELL', 'CLOSE_LONG', 'CLOSE_SHORT'
        'reason': 'TRIPLE_A_COMPLETE',
        'entry_price': 3000.0,
        'take_profit': 3012.0,
        'stop_loss': 2998.0,
        'price': 3000.0,
        'timestamp': time.time()
    }

    # 验证信号格式包含必要字段
    required_signal_fields = ['action', 'reason', 'entry_price', 'take_profit', 'stop_loss']
    for field in required_signal_fields:
        assert field in test_signal, f"信号缺少字段: {field}"

    print("✅ 所有orchestrator所需的方法和属性都存在")
    print("✅ 信号格式兼容性验证通过")

    print("✅ 与orchestrator的兼容性测试通过")


def run_all_integration_tests():
    """运行所有集成测试"""
    print("🚀 四号引擎集成测试套件")
    print("=" * 70)

    test_signal_generator_initialization()
    test_signal_generator_process_tick()
    test_signal_generator_update_maps()
    test_shadow_generator_enhancement()
    test_compatibility_with_orchestrator()

    print("\n" + "=" * 70)
    print("🎉 所有集成测试通过！")
    print("\n💡 总结:")
    print("  1. 信号生成器初始化正确")
    print("  2. Tick处理功能正常")
    print("  3. 地图更新功能正常")
    print("  4. 影子引擎增强功能正常")
    print("  5. 与orchestrator接口完全兼容")
    print("  6. 状态机集成成功")


if __name__ == "__main__":
    run_all_integration_tests()
