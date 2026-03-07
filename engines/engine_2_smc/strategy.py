#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/2/26 11:26 PM
@File       : strategy.py
@Description: 二号引擎 SMC 雷达包装器 (兼容旧版本)
              实际实现已移至 src.strategy.smc_validator
              此文件仅作为包装器保持向后兼容
"""
import asyncio
import datetime
import os
import sys

import numpy as np
import pandas as pd

# 确保能导入 src 目录下的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 导入新的 SMC 验证模块
from src.strategy.smc_validator import MicroSMCRadar
from src.utils.log import get_logger

logger = get_logger(__name__)

# 保持向后兼容，MicroSMCRadar 类现在从 smc_validator 导入
# 所有方法都可用，无需额外定义

if __name__ == "__main__":
    # 简单的本地测试，看看雷达兵能不能正常画出地图
    radar = MicroSMCRadar(symbol="ETH-USDT-SWAP", timeframes=["5m", "15m", "1H"])
    radar.update_structure()
    print("\n🗺️ 当前算出的 5m 支撑防线：")
    for p in radar.active_pois:
        print(f"[{p['type']}] 顶部: {p['top']}, 底部: {p['bottom']}, 生成时间: {p['time']}")

    test_price = 2050.0
    is_safe, msg = radar.is_in_poi(test_price)
    print(f"\n现价 {test_price} 能否抄底？ -> {is_safe} ({msg})")