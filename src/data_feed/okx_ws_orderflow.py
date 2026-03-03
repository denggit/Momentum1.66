#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/3/26 10:14 PM
@File       : okx_ws_orderflow.py
@Description: 
"""
import asyncio
import json
import logging
import websockets
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')


class OrderFlowProcessor:
    def __init__(self, symbol="ETH-USDT-SWAP"):
        self.symbol = symbol
        self.ws_url = "wss://ws.okx.com:8443/ws/v5/public"

        # 核心状态机 (在内存中实时维护)
        self.cvd = 0.0  # 累计成交量差 (Cumulative Volume Delta)
        self.volume_profile = defaultdict(float)  # 价格 -> 成交量映射

        # 用于记录背离的极值
        self.current_price = 0.0
        self.recent_high = float('-inf')
        self.recent_low = float('inf')

    async def connect_and_listen(self):
        """连接 OKX WebSocket 并监听实时成交"""
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"channel": "trades", "instId": self.symbol}]
        }

        while True:
            try:
                logging.info(f"🔄 正在连接 OKX WebSocket 获取 {self.symbol} 订单流...")
                async with websockets.connect(self.ws_url) as ws:
                    # 发送订阅请求
                    await ws.send(json.dumps(subscribe_msg))
                    logging.info("✅ 成功订阅 Trades 频道！")

                    # 持续接收数据
                    while True:
                        response = await ws.recv()
                        data = json.loads(response)

                        if 'data' in data:
                            self._process_trades(data['data'])

            except Exception as e:
                logging.error(f"❌ WebSocket 连接断开，准备重连: {e}")
                await asyncio.sleep(3)  # 断线重连缓冲

    def _process_trades(self, trades):
        """处理每一笔 Tick 级别的成交数据"""
        for trade in trades:
            price = float(trade['px'])
            size = float(trade['sz'])  # 张数或币数
            side = trade['side']  # 'buy' (主动买) 或 'sell' (主动卖)

            self.current_price = price

            # 1. 实时计算 CVD
            if side == 'buy':
                self.cvd += size
            else:
                self.cvd -= size

            # 2. 实时描绘 Volume Profile (价格密集区)
            # 为了统计方便，我们将价格按 tick size 归一化 (比如 ETH 约到 0.5 整数位)
            bucket_price = round(price * 2) / 2
            self.volume_profile[bucket_price] += size

            # 3. 简单的实时监控输出 (你可以看到数字在疯狂跳动)
            # 在实盘中，这里会接入背离检测逻辑
            self._detect_divergence()

    def _detect_divergence(self):
        """简易的异常检测占位符：寻找巨量交易"""
        # 每当 CVD 的绝对值发生重大偏移，或者某个价格区间的量异常大时打印
        # 这里的阈值只是随便设的，为了让你看到效果
        current_vp_vol = self.volume_profile[round(self.current_price * 2) / 2]

        if current_vp_vol > 5000:  # 假设在这个价格点突然成交了 5000 张合约
            logging.warning(
                f"🚨 [机构足迹警告] 价格 {self.current_price} 爆出巨量: {current_vp_vol} 张! 当前 CVD: {self.cvd:.2f}")


if __name__ == "__main__":
    # 独立测试运行
    processor = OrderFlowProcessor(symbol="ETH-USDT-SWAP")

    try:
        asyncio.run(processor.connect_and_listen())
    except KeyboardInterrupt:
        print("\n⏹️ 订单流监听已停止。")