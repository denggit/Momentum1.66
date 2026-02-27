#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/27/26 9:05 PM
@File       : delete_table.py
@Description: 
"""
import sqlite3

# 连接到你的本地数据库
conn = sqlite3.connect('data/crypto_history.db')
cursor = conn.cursor()

try:
    # 直接精准爆破 30m 的表
    cursor.execute("DROP TABLE ETH_USDT_SWAP_30m")
    conn.commit()
    print("✅ 成功删除 30m 的脏数据表！其他周期数据已保留。")
except Exception as e:
    print(f"删除失败或表不存在: {e}")

conn.close()