#!/usr/bin/env python3
"""
四号引擎科考船配置加载器
将YAML配置文件转换为代码期望的数据结构
"""

import os
import sys
from typing import Dict, Any, Optional

import yaml

# 添加项目路径以导入数据类
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    from src.strategy.triplea.data_structures import (
        TripleAEngineConfig,
        MarketConfig,
        DataPipelineConfig,
        RangeBarConfig,
        KDEEngineConfig,
        RiskManagerConfig
    )
except ImportError as e:
    print(f"⚠️  警告: 无法导入数据类: {e}")
    print("将使用字典格式的配置")
    TripleAEngineConfig = None


def load_yaml_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载YAML配置文件

    Args:
        config_path: 配置文件路径，默认为当前目录下的config.yaml

    Returns:
        配置字典
    """
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    return config


def convert_to_engine_config(yaml_config: Dict[str, Any]) -> TripleAEngineConfig:
    """将YAML配置转换为TripleAEngineConfig

    Args:
        yaml_config: YAML配置字典

    Returns:
        TripleAEngineConfig实例
    """
    if TripleAEngineConfig is None:
        raise ImportError("无法导入TripleAEngineConfig，请检查项目路径")

    # 从YAML配置中提取triplea_engine部分
    triplea_config = yaml_config.get('triplea_engine', {})

    # 创建MarketConfig
    market_config = MarketConfig(
        instId=yaml_config.get('trading', {}).get('symbol', 'ETH-USDT-SWAP'),
        tick_size=0.01,  # 默认值
        price_precision=2  # 默认值
    )

    # 创建DataPipelineConfig（使用默认值）
    data_pipeline_config = DataPipelineConfig()

    # 创建RangeBarConfig
    algorithm_config = triplea_config.get('algorithm', {})
    range_bar_config = RangeBarConfig(
        tick_range=20,  # 默认值
        tick_size=0.01,  # 默认值
        max_bar_history=1440  # 默认值
    )

    # 创建KDEEngineConfig（使用默认值）
    kde_engine_config = KDEEngineConfig()

    # 创建RiskManagerConfig
    risk_config = triplea_config.get('risk_management', {})
    risk_manager_config = RiskManagerConfig(
        account_size_usdt=risk_config.get('account_size_usdt', 300.0),
        max_risk_per_trade_pct=risk_config.get('max_risk_per_trade_pct', 5.0),
        stop_loss_ticks=risk_config.get('stop_loss_ticks', 2),
        take_profit_ticks=risk_config.get('take_profit_ticks', 6),
        max_daily_loss_pct=risk_config.get('daily_loss_limit_pct', 5.0)
    )

    # 创建完整的TripleAEngineConfig
    engine_config = TripleAEngineConfig(
        market=market_config,
        data_pipeline=data_pipeline_config,
        range_bar=range_bar_config,
        kde_engine=kde_engine_config,
        risk_manager=risk_manager_config,
        enable_numba_cache=True,
        enable_background_warmup=True,
        enable_cpu_affinity=True
    )

    return engine_config


def get_engine_config(config_path: Optional[str] = None) -> TripleAEngineConfig:
    """获取引擎配置（主入口函数）

    Args:
        config_path: 配置文件路径

    Returns:
        TripleAEngineConfig实例
    """
    yaml_config = load_yaml_config(config_path)
    return convert_to_engine_config(yaml_config)


def get_test_config() -> Dict[str, Any]:
    """获取测试配置（简化版）

    Returns:
        测试配置字典
    """
    return {
        'environment': {
            'type': 'science_vessel',
            'mode': 'simulation'
        },
        'trading': {
            'symbol': 'ETH-USDT-SWAP',
            'leverage': 3
        },
        'triplea_engine': {
            'risk_management': {
                'account_size_usdt': 300.0,
                'max_risk_per_trade_pct': 5.0,
                'stop_loss_ticks': 2,
                'take_profit_ticks': 6,
                'daily_loss_limit_pct': 5.0
            }
        }
    }


if __name__ == "__main__":
    """测试配置加载器"""
    print("🧪 测试配置加载器")
    print("=" * 60)

    try:
        # 测试加载YAML配置
        config = load_yaml_config()
        print(f"✅ YAML配置加载成功，包含 {len(config)} 个顶级配置项")

        # 测试转换为引擎配置
        if TripleAEngineConfig is not None:
            engine_config = convert_to_engine_config(config)
            print(f"✅ 引擎配置转换成功:")
            print(f"   - 交易对: {engine_config.market.instId}")
            print(f"   - 账户规模: {engine_config.risk_manager.account_size_usdt} USDT")
            print(f"   - 单笔风险: {engine_config.risk_manager.max_risk_per_trade_pct}%")
            print(f"   - 止损Tick: {engine_config.risk_manager.stop_loss_ticks}")
            print(f"   - 止盈Tick: {engine_config.risk_manager.take_profit_ticks}")
        else:
            print("⚠️  无法测试引擎配置转换（数据类导入失败）")

    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        import traceback

        traceback.print_exc()

    print("=" * 60)
