#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OKX Tick 频率监控工具 (Tick Frequency Monitor)
路径: tools/monitor_okx_ticks.py

作用：
连接 OKX 公共 WebSocket，实时统计特定交易对（如 ETH-USDT-SWAP）的 Tick 吞吐量。
用于辅助量化引擎（如四号引擎）的参数调优（如评估 1秒/15秒 窗口内的平均 Tick 数量）。
"""

import asyncio
import websockets
import json
import time
from collections import deque
from datetime import datetime

# ================= 配置区 =================
SYMBOL = "ETH-USDT-SWAP"
# 频道选择:
# "trades" (最新成交, 订单流最常用的 Tick)
# "bbo-tbt" (Tick-by-tick 最佳买卖盘，极高频)
CHANNEL = "trades"
WS_URL = "wss://ws.okx.com:8443/ws/v5/public"


# ==========================================

class TickMonitor:
    def __init__(self):
        self.tick_count = 0
        self.history_1m = deque(maxlen=60)  # 记录过去 60 分钟，每分钟的 Tick 数
        self.running = True
        self.start_time = time.time()

    async def reporter(self):
        """后台汇报协程：每隔 60 秒打印一次统计数据"""
        while self.running:
            await asyncio.sleep(60)

            current_time = datetime.now().strftime("%H:%M:%S")
            count_this_min = self.tick_count

            # 记录并清零
            self.history_1m.append(count_this_min)
            self.tick_count = 0

            # 计算平均值
            avg_1m = count_this_min
            avg_5m = sum(list(self.history_1m)[-5:]) / min(5, len(self.history_1m))
            avg_total = sum(self.history_1m) / len(self.history_1m)

            # 换算成秒级数据，方便订单流调参
            tps_1m = avg_1m / 60.0

            print(f"[{current_time}] 📊 {SYMBOL} ({CHANNEL}) 吞吐量统计:")
            print(f"  ├─ 过去 1 分钟: {avg_1m} Ticks ({tps_1m:.1f} Ticks/秒)")
            print(f"  ├─ 过去 5 分钟均值: {avg_5m:.0f} Ticks/分钟")
            print(f"  └─ 全局运行均值: {avg_total:.0f} Ticks/分钟\n")

    async def subscribe(self):
        """连接 WebSocket 并接收数据"""
        print(f"🚀 正在连接 OKX WebSocket...\n目标: {SYMBOL} | 频道: {CHANNEL}\n(请等待 60 秒获取首次输出...)")

        async with websockets.connect(WS_URL) as ws:
            sub_msg = {
                "op": "subscribe",
                "args": [{"channel": CHANNEL, "instId": SYMBOL}]
            }
            await ws.send(json.dumps(sub_msg))

            # 启动汇报协程
            asyncio.create_task(self.reporter())

            try:
                async for msg in ws:
                    data = json.loads(msg)
                    if "data" in data:
                        # OKX 的一次推送 (msg) 可能包含多个 Tick 数据，所以要用 len()
                        self.tick_count += len(data["data"])
            except websockets.exceptions.ConnectionClosed:
                print("❌ WebSocket 连接已断开。")
            except Exception as e:
                print(f"❌ 发生异常: {e}")
            finally:
                self.running = False


async def main():
    monitor = TickMonitor()
    await monitor.subscribe()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 监控已手动停止。")