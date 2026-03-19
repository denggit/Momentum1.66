"""
四号引擎v3.0 配置加载器
"""

import os
from typing import Dict, Any, Optional
import yaml

from src.utils.log import get_logger

logger = get_logger(__name__)


def load_triplea_config(
    symbol: str = "default",
    config_type: str = "engine"
) -> Dict[str, Any]:
    """
    加载四号引擎配置

    Args:
        symbol: 交易对名称，如 "ETH-USDT-SWAP"
        config_type: 配置类型，如 "engine", "performance", "risk"

    Returns:
        配置字典
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # 首先尝试加载交易对特定配置
    symbol_config_path = os.path.join(current_dir, f"{symbol}.yaml")

    # 然后加载类型特定配置
    type_config_path = os.path.join(current_dir, f"{config_type}.yaml")

    # 最后加载默认配置
    default_config_path = os.path.join(current_dir, "default.yaml")

    config = {}

    # 加载默认配置
    if os.path.exists(default_config_path):
        with open(default_config_path, 'r', encoding='utf-8') as f:
            default_config = yaml.safe_load(f) or {}
            config.update(default_config)
            logger.debug(f"加载默认配置: {default_config_path}")

    # 加载类型特定配置
    if os.path.exists(type_config_path):
        with open(type_config_path, 'r', encoding='utf-8') as f:
            type_config = yaml.safe_load(f) or {}
            # 深度合并配置
            _deep_update(config, type_config)
            logger.debug(f"加载类型配置: {type_config_path}")

    # 加载交易对特定配置
    if os.path.exists(symbol_config_path):
        with open(symbol_config_path, 'r', encoding='utf-8') as f:
            symbol_config = yaml.safe_load(f) or {}
            # 深度合并配置，交易对配置优先级最高
            _deep_update(config, symbol_config)
            logger.debug(f"加载交易对配置: {symbol_config_path}")

    # 如果没有任何配置，返回空字典
    if not config:
        logger.warning(f"未找到四号引擎配置文件，使用默认值")

    return config


def _deep_update(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """递归合并source字典到target字典，source值覆盖target值"""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def get_triplea_config_value(
    config: Dict[str, Any],
    key_path: str,
    default: Any = None
) -> Any:
    """
    通过点分隔的路径获取配置值

    Args:
        config: 配置字典
        key_path: 点分隔的键路径，如 "process_pool.max_workers"
        default: 默认值

    Returns:
        配置值
    """
    keys = key_path.split('.')
    current = config

    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default

    return current


# 全局配置缓存
_config_cache: Dict[str, Dict[str, Any]] = {}


def get_cached_triplea_config(
    symbol: str = "default",
    config_type: str = "engine"
) -> Dict[str, Any]:
    """获取缓存的配置"""
    cache_key = f"{symbol}:{config_type}"

    if cache_key not in _config_cache:
        _config_cache[cache_key] = load_triplea_config(symbol, config_type)

    return _config_cache[cache_key]