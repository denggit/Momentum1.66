#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/2/26 11:25 PM
@File       : main_bot.py
@Description: 
"""
# 伪代码示例
from engines.engine_2_smc import run_engine_2

if __name__ == "__main__":
    print("🚀 量化总控系统启动...")
    # engine_1.start()  # 暂未开发，注释掉

    print("⚔️ 启动二号主力引擎: SMC 波段猎手")
    run_engine_2(symbol="ETH-USDT-SWAP")

    # engine_3.start()  # 暂未开发，注释掉