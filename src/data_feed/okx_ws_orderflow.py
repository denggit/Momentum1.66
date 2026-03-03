import asyncio
import json
import logging
import os
import sys
import websockets
from collections import deque
import time

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.log import get_logger
logger = get_logger(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')


class OrderFlowSniper:
    def __init__(self, symbol="ETH-USDT-SWAP"):
        self.symbol = symbol
        # 使用 AWS 专线域名，在东京节点极其稳定
        self.ws_url = "wss://ws.okx.com:8443/ws/v5/public"

        # 实时状态
        self.cvd = 0.0
        self.current_price = 0.0

        # 内存降维：使用双端队列存储过去 300 个“10秒快照” (回看过去 50 分钟)
        self.snapshots = deque(maxlen=300)
        self.last_snapshot_time = time.time()
        self.last_heartbeat = time.time()

    async def connect_and_listen(self):
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"channel": "trades", "instId": self.symbol}]
        }

        while True:
            try:
                logger.info(f"🚀 正在连接 OKX 订单流极速通道 ({self.symbol})...")
                async with websockets.connect(self.ws_url) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("✅ 接入成功！开启微观多空肉搏监控 (静默模式)...")

                    while True:
                        response = await ws.recv()
                        data = json.loads(response)

                        if 'data' in data:
                            self._process_ticks(data['data'])

            except Exception as e:
                logger.error(f"❌ 链路断开，准备重连: {e}")
                await asyncio.sleep(3)

    def _process_ticks(self, trades):
        current_ts = time.time()

        for trade in trades:
            self.current_price = float(trade['px'])
            size = float(trade['sz'])
            side = trade['side']

            # CVD 核心累计 (买入增加，卖出减少)
            if side == 'buy':
                self.cvd += size
            else:
                self.cvd -= size

        # 1. 触发快照与背离检测机制 (每 10 秒拍一次照，绝不刷屏)
        if current_ts - self.last_snapshot_time >= 10:
            self._take_snapshot(current_ts)
            self._detect_absorption_divergence()
            self.last_snapshot_time = current_ts

        # 2. 心跳日志 (每 1 分钟报备一次，让你知道它没死机)
        if current_ts - self.last_heartbeat >= 60:
            logger.info(
                f"💓 [雷达扫掠中] 现价: {self.current_price} | 当前 CVD: {self.cvd:.1f} | 已存快照: {len(self.snapshots)}/300")
            self.last_heartbeat = current_ts

    def _take_snapshot(self, ts):
        """将当前的价格和 CVD 压入历史窗口"""
        self.snapshots.append({
            'ts': ts,
            'price': self.current_price,
            'cvd': self.cvd
        })

    def _detect_absorption_divergence(self):
        """🌟 核心武器：检测机构恐慌吸收 (散户血洗，机构抄底)"""
        if len(self.snapshots) < 30:
            return

        past_snapshots = list(self.snapshots)[:-1]
        lowest_snap = min(past_snapshots, key=lambda x: x['price'])
        current_snap = self.snapshots[-1]

        # 条件 A: 价格在低位摩擦 (没有暴跌下去)
        price_is_near_low = current_snap['price'] <= (lowest_snap['price'] + 1.0)

        # 条件 B: 真实的 USDT 净流出量！
        cvd_delta_contracts = current_snap['cvd'] - lowest_snap['cvd']
        CONTRACT_SIZE = 0.1
        cvd_delta_usdt = cvd_delta_contracts * CONTRACT_SIZE * current_snap['price']

        # 🌟 抄底核心逻辑：CVD 出现巨额负数 (散户疯狂市价抛售)
        # 设定阈值：-80 万美金！说明散户砸了 80万美金的市价空单，但价格居然没跌穿！
        massive_selling_absorbed = cvd_delta_usdt < -800_000

        time_passed = (current_snap['ts'] - lowest_snap['ts']) > 20

        if price_is_near_low and massive_selling_absorbed and time_passed:
            time_diff = int(current_snap['ts'] - lowest_snap['ts']) / 60
            logger.warning("\n" + "🟢" * 25)
            logger.warning(f"🚨 [抄底绝杀] 发现深海冰山！散户正在被血洗！")
            logger.warning(
                f"💥 异动数据: 在过去 {time_diff:.1f} 分钟内，市场涌入了 ${abs(cvd_delta_usdt) / 10000:.1f} 万美金的市价砸盘！")
            logger.warning(f"🛡️ 盘口真相: 价格被死死托在 {current_snap['price']} 附近没有崩盘。")
            logger.warning("🎯 战术结论: 机构正在用限价买单疯狂吸收带血的筹码，随时准备反抽！")
            logger.warning("🟢" * 25 + "\n")

            for _ in range(150):
                if self.snapshots: self.snapshots.popleft()


if __name__ == "__main__":
    sniper = OrderFlowSniper(symbol="ETH-USDT-SWAP")
    try:
        asyncio.run(sniper.connect_and_listen())
    except KeyboardInterrupt:
        logger.info("\n⏹️ 订单流狙击手已安全撤离。")