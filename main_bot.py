#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/2/26 11:25 PM
@File       : main_bot.py
@Description:
"""
import os
import sys

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(current_file)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 伪代码示例
from engines.engine_2_smc import run_engine_2
from src.utils.log import get_logger
logger = get_logger(__name__)

if __name__ == "__main__":
    logger.info("🚀 量化总控系统启动...")
    # engine_1.start()  # 暂未开发，注释掉

    logger.info("⚔️ 启动二号主力引擎: SMC 波段猎手")
    run_engine_2(symbol="ETH-USDT-SWAP")

    # engine_3.start()  # 暂未开发，注释掉