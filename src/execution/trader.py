# !/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26
@File       : trader.py
@Description: 极速订单执行器 (Taker买入 -> Maker止盈 -> 条件止损)
"""
import base64
import hmac
import json
import asyncio
import datetime
import requests
import sys
import os

current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(current_file))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.log import get_logger
from config.env_loader import OKX_CONFIG

logger = get_logger(__name__)


class OKXTrader:
    def __init__(self, symbol="ETH-USDT-SWAP", leverage=20, td_mode="cross", risk_pct=0.5):
        self.symbol = symbol
        self.api_key = OKX_CONFIG.get('api_key')
        self.secret_key = OKX_CONFIG.get('secret_key')
        self.passphrase = OKX_CONFIG.get('passphrase')
        self.base_url = "https://www.okx.com"

        self.leverage = leverage
        self.td_mode = td_mode
        self.risk_pct = risk_pct  # 🌟 比如 0.5 表示每次下注可用余额的 50%
        self.available_usdt = 0.0  # 🌟 缓存在本地的可用余额

        # 简单合约面值表 (1张合约等于多少个币)
        self.ct_val_map = {
            "ETH-USDT-SWAP": 0.1,
            "BTC-USDT-SWAP": 0.01,
            "SOL-USDT-SWAP": 1.0,
            "DOGE-USDT-SWAP": 100.0
        }

        if not self.api_key or not self.secret_key:
            logger.error("⚠️ OKX API 密钥未配置，请检查 .env 文件！实盘将无法执行下单。")

    def _get_signature(self, timestamp, method, request_path, body):
        message = str(timestamp) + str(method) + str(request_path) + str(body)
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf8'),
            bytes(message, encoding='utf-8'),
            digestmod='sha256'
        )
        d = mac.digest()
        return base64.b64encode(d).decode('utf-8')

    def _get_headers(self, method, request_path, body=""):
        timestamp = datetime.datetime.utcnow().isoformat()[:-3] + 'Z'
        sign = self._get_signature(timestamp, method, request_path, body)
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": str(timestamp),
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

    async def _request(self, method, endpoint, payload=None):
        """异步非阻塞请求 OKX API"""

        def do_request():
            url = self.base_url + endpoint
            body_str = json.dumps(payload) if payload else ""
            headers = self._get_headers(method, endpoint, body_str)
            try:
                if method == 'POST':
                    res = requests.post(url, data=body_str, headers=headers, timeout=5)
                else:
                    res = requests.get(url, headers=headers, timeout=5)
                return res.json()
            except Exception as e:
                logger.error(f"API 请求异常: {e}")
                return None

        return await asyncio.to_thread(do_request)

    async def execute_snipe(self, price: float, local_low: float):
        """
        极速三连发执行器：市价开多 -> Maker止盈 -> 条件市价止损
        """
        if not self.api_key:
            logger.error("❌ 实盘 API 未配置，拒绝下单。")
            return

        # 🌟 动态计算本次应下注的本金
        if self.available_usdt <= 0:
            logger.error("❌ 本地缓存余额不足或未获取到余额，放弃本次开火！")
            return

        risk_usdt = self.available_usdt * self.risk_pct

        # ==========================================
        # 1. 计算仓位大小 (合约张数)
        # ==========================================
        ct_val = self.ct_val_map.get(self.symbol, 1.0)

        # 实际名义价值 = 动用资金 * 杠杆倍数
        notional_value = risk_usdt * self.leverage
        # 购买张数 = 名义价值 / 现价 / 每张面值
        sz = int(notional_value / price / ct_val)

        if sz <= 0:
            logger.error(f"⚠️ 计算出的仓位太小 (张数: {sz})，无法下单！检查你的 risk_usdt。")
            return

        logger.warning(f"🔫 [实盘执行] 正在扣动扳机！目标张数: {sz} 张 (名义价值: ${notional_value:.2f})")

        # ==========================================
        # 2. 市价吃单 (Taker Buy)
        # ==========================================
        order_payload = {
            "instId": self.symbol,
            "tdMode": self.td_mode,
            "side": "buy",
            "ordType": "market",
            "sz": str(sz)
        }

        logger.info("📡 [1/3] 发送市价开仓请求...")
        res_buy = await self._request("POST", "/api/v5/trade/order", order_payload)

        if not res_buy or res_buy.get('code') != '0':
            logger.error(f"❌ 市价买入失败: {res_buy}")
            return

        logger.info(f"✅ 市价开多成功！订单号: {res_buy['data'][0]['ordId']}")

        # 稍微等 100ms 确保仓位已经结算到账户，防止 reduceOnly 报错
        await asyncio.sleep(0.1)

        # ==========================================
        # 3. 挂出 Post-only 止盈单 (Maker Sell)
        # ==========================================
        tp_price = round(price * 1.004, 2)  # 0.4% 止盈
        tp_payload = {
            "instId": self.symbol,
            "tdMode": self.td_mode,
            "side": "sell",
            "ordType": "post_only",  # 🌟 严格只做 Maker，白嫖手续费
            "sz": str(sz),
            "px": str(tp_price),
            "reduceOnly": True  # 🌟 关键：只减仓
        }

        logger.info(f"📡 [2/3] 发送止盈单请求 (Post-only) -> 目标价: {tp_price}")
        res_tp = await self._request("POST", "/api/v5/trade/order", tp_payload)
        if res_tp and res_tp.get('code') == '0':
            logger.info(f"✅ 止盈单已架设！")
        else:
            logger.error(f"❌ 止盈单架设失败: {res_tp}")

        # ==========================================
        # 4. 挂出条件止损单 (Conditional Market Sell)
        # ==========================================
        # 止损设在坑底下 0.05%
        sl_price = round(local_low * 0.9995, 2)
        sl_payload = {
            "instId": self.symbol,
            "tdMode": self.td_mode,
            "side": "sell",
            "ordType": "conditional",
            "sz": str(sz),
            "triggerPx": str(sl_price),
            "triggerPxType": "last",
            "ordPx": "-1",  # -1 代表触发后市价砸盘逃跑
            "reduceOnly": True  # 🌟 关键：只减仓
        }

        logger.info(f"📡 [3/3] 发送止损单请求 (条件市价) -> 触发价: {sl_price}")
        res_sl = await self._request("POST", "/api/v5/trade/order-algo", sl_payload)
        if res_sl and res_sl.get('code') == '0':
            logger.info(f"✅ 止损单已架设！")
        else:
            logger.error(f"❌ 止损单架设失败: {res_sl}")

        logger.warning("🏁 [三连发完毕] 交易已托管给交易所，等待止盈或止损触发！")

        # 🌟 (可选) 刚开完仓，余额肯定变了，直接主动触发一次查账
        asyncio.create_task(self.fetch_balance())

    async def update_balance_loop(self):
        """🌟 后台闲时查账协程：每隔 60 秒查询一次余额，缓存在本地"""
        logger.info("💰 [财务官] 已上线！将在后台默默监控账户余额...")
        while True:
            await self.fetch_balance()
            await asyncio.sleep(60)  # 闲时每分钟查一次

    async def fetch_balance(self):
        """请求 OKX 获取 USDT 可用余额"""
        res = await self._request("GET", "/api/v5/account/balance")
        if res and res.get('code') == '0':
            details = res['data'][0]['details']
            for asset in details:
                if asset['ccy'] == 'USDT':
                    self.available_usdt = float(asset['availEq'])
                    logger.debug(f"💵 [闲时查账] 当前账户可用 USDT: {self.available_usdt:.2f}")
                    break