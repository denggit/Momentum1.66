import asyncio
import json
import logging
from collections import deque

import websockets

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')


class OrderFlowSniper:
    def __init__(self, symbol="ETH-USDT-SWAP"):
        self.symbol = symbol
        self.ws_url = "wss://ws.okx.com:8443/ws/v5/public"

        # 实时状态
        self.cvd = 0.0
        self.current_price = 0.0

        # 内存降维：使用双端队列存储过去 300 个“10秒快照” (相当于回看过去 50 分钟的微观结构)
        self.snapshots = deque(maxlen=300)
        self.last_snapshot_time = 0

    async def connect_and_listen(self):
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"channel": "trades", "instId": self.symbol}]
        }

        while True:
            try:
                logging.info(f"🚀 正在连接 OKX 订单流极速通道 ({self.symbol})...")
                async with websockets.connect(self.ws_url) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    logging.info("✅ 接入成功！开启微观多空肉搏监控...")

                    while True:
                        response = await ws.recv()
                        data = json.loads(response)

                        if 'data' in data:
                            self._process_ticks(data['data'])

            except Exception as e:
                logging.error(f"❌ 链路断开，准备重连: {e}")
                await asyncio.sleep(3)

    def _process_ticks(self, trades):
        current_ts = 0
        for trade in trades:
            self.current_price = float(trade['px'])
            size = float(trade['sz'])
            side = trade['side']
            current_ts = int(trade['ts']) / 1000.0  # 转换为秒

            # CVD 核心累计
            if side == 'buy':
                self.cvd += size
            else:
                self.cvd -= size

        # 触发快照与背离检测机制 (每 10 秒拍一次照)
        if current_ts - self.last_snapshot_time >= 10:
            self._take_snapshot(current_ts)
            self._detect_absorption_divergence()
            self.last_snapshot_time = current_ts

    def _take_snapshot(self, ts):
        """将当前的价格和 CVD 压入历史窗口"""
        self.snapshots.append({
            'ts': ts,
            'price': self.current_price,
            'cvd': self.cvd
        })

    def _detect_absorption_divergence(self):
        """🌟 核心武器：检测机构底背离 (吸收)"""
        if len(self.snapshots) < 30:
            return  # 数据太少，先攒至少 5 分钟的快照

        # 1. 找出过去 50 分钟内，价格最低的那个瞬间 (前低点)
        past_snapshots = list(self.snapshots)[:-1]  # 排除当前这一个
        lowest_snap = min(past_snapshots, key=lambda x: x['price'])

        current_snap = self.snapshots[-1]

        # 2. 数学背离判定逻辑：
        # 条件 A: 当前价格比之前的极小值还要低（或者非常接近，比如相差不到 1 刀），说明在向下插针猎杀止损。
        price_is_lower = current_snap['price'] <= (lowest_snap['price'] + 1.0)

        # 条件 B: 当前的 CVD 却比那时候的 CVD 高出了很多 (比如高出 500 张合约)
        # 说明这期间虽然价格砸下去了，但大部分是假动能，主动卖盘被冰山买单吸干了。
        cvd_is_higher = current_snap['cvd'] > (lowest_snap['cvd'] + 500)

        if price_is_lower and cvd_is_higher:
            time_diff = int(current_snap['ts'] - lowest_snap['ts']) / 60
            logging.warning("\n" + "🔥" * 20)
            logging.warning(f"🚨 [机构底背离预警 - 发现冰山买盘吸收！]")
            logging.warning(
                f"📍 前低点 ({time_diff:.1f}分钟前): 价格 {lowest_snap['price']} | CVD {lowest_snap['cvd']:.1f}")
            logging.warning(
                f"💥 当前点 (插针中): 价格 {current_snap['price']} | CVD {current_snap['cvd']:.1f} (强劲抬升!)")
            logging.warning("🎯 结论: 散户在低位抛售，但机构正在疯狂吸筹，随时可能爆拉！准备市价做多！")
            logging.warning("🔥" * 20 + "\n")

            # 找到背离后，为了防止疯狂报警，清空前一半的队列
            for _ in range(150): self.snapshots.popleft()


if __name__ == "__main__":
    sniper = OrderFlowSniper(symbol="ETH-USDT-SWAP")
    try:
        asyncio.run(sniper.connect_and_listen())
    except KeyboardInterrupt:
        print("\n⏹️ 订单流监听已安全停止。")
