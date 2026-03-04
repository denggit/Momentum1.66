import asyncio
import json
import logging
import os
import sys
import websockets
from collections import deque
import time
import datetime

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.log import get_logger
from src.utils.email_sender import send_trading_signal_email
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

        # 邮件发送频率控制 (至少间隔10分钟，避免重复报警)
        self._last_email_sent_time = 0
        self._email_cooldown = 600  # 10分钟，单位秒

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
            logger.debug(
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
        """🌟 核心武器：带有【流速检测】的恐慌吸收狙击"""
        if len(self.snapshots) < 30:  # 确保有足够的数据
            return

        past_snapshots = list(self.snapshots)[:-1]
        lowest_snap = min(past_snapshots, key=lambda x: x['price'])
        current_snap = self.snapshots[-1]

        # 1. 位置确认（底线箱体）
        price_diff = current_snap['price'] - lowest_snap['price']
        price_is_near_low = -2.0 <= price_diff <= 1.0
        
        # 2. 宏观爆量确认（3分钟流速）
        RECENT_WINDOW = 18 # 180秒
        snapshot_3min_ago = self.snapshots[-RECENT_WINDOW]
        recent_cvd_delta_contracts = current_snap['cvd'] - snapshot_3min_ago['cvd']
        CONTRACT_SIZE = 0.1 
        recent_cvd_delta_usdt = recent_cvd_delta_contracts * CONTRACT_SIZE * current_snap['price']
        massive_selling_absorbed = recent_cvd_delta_usdt < -500_000 

        # ==========================================
        # 🧠 V5.1 极光雷达：要求极其明确的多头主力反击！
        # ==========================================
        last_snap = self.snapshots[-2]
        micro_cvd_delta_contracts = current_snap['cvd'] - last_snap['cvd']
        micro_cvd_delta_usdt = micro_cvd_delta_contracts * CONTRACT_SIZE * current_snap['price']
        
        # 核心防御：最后的 10 秒钟，不能仅仅是跌停了，必须有多头主动砸进至少 5 万美金的市价买单拉升！
        is_turning_around = micro_cvd_delta_usdt > 50_000
        
        time_passed = (current_snap['ts'] - lowest_snap['ts']) > 20

        # 把这把安全锁加进终极判定里！
        if price_is_near_low and massive_selling_absorbed and is_turning_around and time_passed:
            logger.warning("\n" + "🟢" * 25)
            logger.warning(f"🚨 [流速级抄底绝杀] 发现深海冰山！散户正在被集中血洗！")
            logger.warning(
                f"💥 爆量数据: 就在刚刚的 【3分钟】 内，市场瞬间涌入了 ${abs(recent_cvd_delta_usdt) / 10000:.1f} 万美金的市价砸盘！")
            logger.warning(f"🛡️ 盘口真相: 价格被死死托在 {current_snap['price']} 附近，根本跌不下去。")
            logger.warning("🎯 战术结论: 典型的抛售高潮 (Selling Climax) + 机构限价吸收！准备抢反弹！")
            logger.warning("🟢" * 25 + "\n")

            # 异步发送邮件通知
            asyncio.create_task(self._send_bottom_fishing_email(
                symbol=self.symbol,
                price=current_snap['price'],
                cvd_delta_usdt=recent_cvd_delta_usdt,
                time_window_minutes=3.0
            ))

            # 冷却：清空一半的数据，防止一波行情里反复报警
            for _ in range(150):
                if self.snapshots: self.snapshots.popleft()

    async def _send_bottom_fishing_email(self, symbol: str, price: float,
                                        cvd_delta_usdt: float, time_window_minutes: float):
        """
        发送抄底机会邮件通知。

        Args:
            symbol: 交易对符号
            price: 当前价格
            cvd_delta_usdt: CVD 变化的 USDT 金额（负值表示净卖出）
            time_window_minutes: 检测时间窗口（分钟）
        """
        # 频率控制：至少间隔指定时间才能再次发送邮件
        current_time = time.time()
        if current_time - self._last_email_sent_time < self._email_cooldown:
            logger.debug(f"邮件发送频率限制，跳过本次发送。还需等待 {self._email_cooldown - (current_time - self._last_email_sent_time):.0f} 秒")
            return

        try:
            signal_type = "流速级抄底绝杀"
            # 获取当前时间戳
            detection_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            details = f"""
🚨 检测到机构恐慌吸收信号！

📊 交易对: {symbol}
🕐 检测时间: {detection_time}
💰 当前价格: {price:.2f}
📉 3分钟内净卖出: ${abs(cvd_delta_usdt):,.0f} USDT
⏱️ 时间窗口: {time_window_minutes} 分钟
📈 信号类型: {signal_type}

💡 核心逻辑:
1. 价格被压制在近期低点附近
2. 散户在短时间内疯狂抛售 {abs(cvd_delta_usdt)/10000:.1f} 万美金
3. 价格未能继续下跌，显示机构在限价吸收

🎯 操作建议: 准备抢反弹，关注短期反转机会
            """

            success = await send_trading_signal_email(
                symbol=symbol,
                signal_type=signal_type,
                price=price,
                details=details
            )

            if success:
                self._last_email_sent_time = time.time()
                logger.info("✅ 抄底机会邮件发送成功")
            else:
                logger.warning("⚠️ 抄底机会邮件发送失败")

        except Exception as e:
            logger.error(f"发送抄底机会邮件时发生错误: {e}")


if __name__ == "__main__":
    sniper = OrderFlowSniper(symbol="ETH-USDT-SWAP")
    try:
        asyncio.run(sniper.connect_and_listen())
    except KeyboardInterrupt:
        logger.info("\n⏹️ 订单流狙击手已安全撤离。")
