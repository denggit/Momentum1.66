import asyncio
import json
import logging
import websockets
from collections import deque
import time
from src.utils.log import get_logger
logger = get_logger(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')


class OrderFlowSniper:
    def __init__(self, symbol="ETH-USDT-SWAP"):
        self.symbol = symbol
        # 使用 AWS 专线域名，在东京节点极其稳定
        self.ws_url = "wss://wsaws.okx.com:8443/ws/v5/public"

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
        """🌟 核心武器：检测机构底背离 (吸收)"""
        # 数据太少时先不计算，攒够至少 5 分钟 (30个快照) 再开始巡逻
        if len(self.snapshots) < 30:
            return

            # 找出过去 50 分钟内，价格最低的那个瞬间 (前低点)
        past_snapshots = list(self.snapshots)[:-1]
        lowest_snap = min(past_snapshots, key=lambda x: x['price'])
        current_snap = self.snapshots[-1]

        # ==========================================
        # 🧠 V3 进阶版：真实购买力（USDT）动态背离逻辑
        # ==========================================
        price_is_lower = current_snap['price'] <= (lowest_snap['price'] + 0.5)

        # 1. 计算这期间 CVD 增加了多少“张”
        cvd_delta_contracts = current_snap['cvd'] - lowest_snap['cvd']

        # 2. 🌟 核心升维：转化为真实的 USDT 净买入金额！
        # 在 OKX，ETH 永续合约单张面值为 0.1 ETH (如果你以后换 BTC，这里改成对应的面值)
        CONTRACT_SIZE = 0.1
        cvd_delta_usdt = cvd_delta_contracts * CONTRACT_SIZE * current_snap['price']

        # 3. 设定动态美元阈值：底背离必须伴随至少 "50万美金" 的真金白银净流入！
        cvd_is_higher = cvd_delta_usdt > 500_000

        time_passed = (current_snap['ts'] - lowest_snap['ts']) > 30

        if price_is_lower and cvd_is_higher and time_passed:
            time_diff = int(current_snap['ts'] - lowest_snap['ts']) / 60
            logger.warning("\n" + "🔥" * 25)
            # 报警日志里，打印出真实的美元异动！
            logger.warning(
                f"🚨 发现冰山！前低点已被砸穿，但主力在此期间净买入了 ${cvd_delta_usdt / 10000:.1f} 万美金的筹码！")
            logger.warning(
                f"📍 前低点 ({time_diff:.1f}分钟前): 价格 {lowest_snap['price']} | CVD {lowest_snap['cvd']:.1f}")
            logger.warning(
                f"💥 当前点 (插针中): 价格 {current_snap['price']} | CVD {current_snap['cvd']:.1f} (CVD 强劲抬升!)")
            logger.warning("🎯 战术结论: 散户砸盘被全部吸收，随时可能爆拉！")
            logger.warning("🔥" * 25 + "\n")

            # 找到一次背离后，为了防止余震疯狂报警，清空一半的队列进入“冷却期”
            for _ in range(150):
                if self.snapshots:
                    self.snapshots.popleft()


if __name__ == "__main__":
    sniper = OrderFlowSniper(symbol="ETH-USDT-SWAP")
    try:
        asyncio.run(sniper.connect_and_listen())
    except KeyboardInterrupt:
        logger.info("\n⏹️ 订单流狙击手已安全撤离。")