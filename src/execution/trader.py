# !/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26
@File       : trader.py
@Description: 极速订单执行器 (Taker买入 -> Maker止盈 -> 条件止损)
"""
import asyncio
import base64
import datetime
import hmac
import json
import os
import sys

import requests

current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(current_file))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.log import get_logger
from config.env_loader import OKX_CONFIG
from config.loader import GLOBAL_SETTINGS
from dataclasses import dataclass
from typing import Optional, Dict, List

logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    """交易执行结果数据类"""
    symbol: str
    entry_price: float
    tp1_price: float
    tp2_price: float
    local_low: float
    position_size: float  # 合约张数
    tp1_order_id: Optional[str] = None
    tp2_order_id: Optional[str] = None
    sl_algo_id: Optional[str] = None
    remaining_size: Optional[float] = None  # TP1成交后剩余张数


class OKXTrader:
    def __init__(self, symbol="ETH-USDT-SWAP", leverage=20, td_mode="cross", risk_pct=0.5, sl_pct=0.0015, context=None):
        self.symbol = symbol
        self.api_key = OKX_CONFIG.get('api_key')
        self.secret_key = OKX_CONFIG.get('secret_key')
        self.passphrase = OKX_CONFIG.get('passphrase')
        self.base_url = "https://www.okx.com"

        self.leverage = leverage
        self.td_mode = td_mode
        self.risk_pct = risk_pct  # 🌟 比如 0.5 表示每次下注可用余额的 50%
        self.sl_pct = sl_pct  # 止损百分比
        self.available_usdt = 0.0  # 🌟 缓存在本地的可用余额
        self.is_in_position = False  # 默认为空仓（向后兼容）

        self.context = context  # MarketContext实例（可选）

        # 简单合约面值表 (1张合约等于多少个币)，从全局配置获取
        self.ct_val_map = GLOBAL_SETTINGS.get("contract_values", {
            "ETH-USDT-SWAP": 0.1,
            "BTC-USDT-SWAP": 0.01,
            "SOL-USDT-SWAP": 1.0,
            "DOGE-USDT-SWAP": 100.0
        })

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

    # ==================== 原子化API方法 ====================

    async def get_order_status(self, order_id: str) -> str:
        """获取订单状态"""
        try:
            res = await self._request("GET", f"/api/v5/trade/order?instId={self.symbol}&ordId={order_id}")
            if res and res.get('code') == '0':
                return res['data'][0]['state']
            return 'unknown'
        except Exception as e:
            logger.error(f"[Trader] 获取订单状态异常: {e}")
            return 'unknown'

    async def cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        try:
            payload = {"instId": self.symbol, "ordId": order_id}
            res = await self._request("POST", "/api/v5/trade/cancel-order", payload)
            return res and res.get('code') == '0'
        except Exception as e:
            logger.error(f"[Trader] 取消订单异常: {e}")
            return False

    async def cancel_algo_order(self, algo_id: str) -> bool:
        """取消算法订单（止损单）"""
        try:
            payload = [{"instId": self.symbol, "algoId": algo_id}]
            res = await self._request("POST", "/api/v5/trade/cancel-algos", payload)
            return res and res.get('code') == '0'
        except Exception as e:
            logger.error(f"[Trader] 取消算法订单异常: {e}")
            return False

    async def create_stop_loss_order(self, size: float, trigger_price: float) -> Optional[str]:
        """创建止损订单"""
        try:
            payload = {
                "instId": self.symbol,
                "tdMode": self.td_mode,
                "side": "sell",
                "ordType": "conditional",
                "sz": str(size),
                "slTriggerPx": str(trigger_price),
                "slTriggerPxType": "last",
                "slOrdPx": "-1",
                "reduceOnly": True
            }
            res = await self._request("POST", "/api/v5/trade/order-algo", payload)
            if res and res.get('code') == '0':
                return res['data'][0]['algoId']
            return None
        except Exception as e:
            logger.error(f"[Trader] 创建止损订单异常: {e}")
            return None

    async def get_klines(self, timeframe: str = "5m", limit: int = 15) -> List[Dict]:
        """获取K线数据"""
        try:
            res = await self._request("GET",
                                      f"/api/v5/market/candles?instId={self.symbol}&bar={timeframe}&limit={limit}")
            if res and res.get('code') == '0':
                return res['data']
            return []
        except Exception as e:
            logger.error(f"[Trader] 获取K线数据异常: {e}")
            return []

    async def market_buy(self, size: float, reduce_only: bool = False) -> Dict:
        payload = {
            "instId": self.symbol, "tdMode": self.td_mode, "side": "buy",
            "ordType": "market", "sz": str(int(size))
        }
        if reduce_only: payload["reduceOnly"] = True
        return await self._request("POST", "/api/v5/trade/order", payload)

    async def market_sell(self, size: float, reduce_only: bool = False) -> Dict:
        payload = {
            "instId": self.symbol, "tdMode": self.td_mode, "side": "sell",
            "ordType": "market", "sz": str(int(size))
        }
        if reduce_only: payload["reduceOnly"] = True
        return await self._request("POST", "/api/v5/trade/order", payload)

    async def post_only_sell(self, size: float, price: float) -> Dict:
        """Maker卖出"""
        payload = {
            "instId": self.symbol,
            "tdMode": self.td_mode,
            "side": "sell",
            "ordType": "post_only",
            "sz": str(size),
            "px": str(price),
            "reduceOnly": True
        }
        return await self._request("POST", "/api/v5/trade/order", payload)

    # ==================== 核心交易执行 ====================

    async def update_balance_loop(self):
        """🌟 后台闲时查账协程：每隔 60 秒查询一次余额，缓存在本地"""
        logger.info("💰 [财务官] 已上线！将在后台默默监控账户余额...")

        # 🌟 新增：启动时第一件事，先把枪管的威力（杠杆）调好！
        await self.set_leverage_on_startup()

        while True:
            await self.fetch_balance()
            # 检查持仓状态（优先使用context）
            in_position = self.context.is_in_position if self.context else self.is_in_position
            if in_position:
                # 战时模式：手里有单子，随时可能止盈、止损或打保本
                # 财务官每 5 秒死死盯住账户，一旦发现单子没了，立刻光速解锁！
                await asyncio.sleep(5)
            else:
                # 闲时模式：空仓状态，不需要浪费 API 额度，60 秒查一次余额即可
                await asyncio.sleep(60)

    async def fetch_balance(self):
        """请求 OKX 获取 USDT 可用余额"""
        # 查询当前品种持仓
        pos_res = await self._request("GET", f"/api/v5/account/positions?instId={self.symbol}")
        if pos_res and pos_res.get('code') == '0':
            positions = pos_res.get('data', [])
            has_pos = any(abs(float(p.get('pos', 0))) > 0.05 for p in positions)

            if has_pos:
                self.is_in_position = True
                if self.context:
                    self.context.is_in_position = True
            else:
                # 只有当我们本身没有强制持仓时，才设为 False
                self.is_in_position = False
                if self.context:
                    # 🆕 检测到仓位消失，清除持仓信息并触发事件
                    if self.context.is_in_position:
                        logger.warning(f"💰 [财务官] 检测到仓位已消失（可能被手动平仓），清除持仓状态")
                        self.context.clear_position()  # 这会触发position_updated事件
                    else:
                        self.context.is_in_position = False

        # 查询当前余额
        balance_res = await self._request("GET", "/api/v5/account/balance")
        if balance_res and balance_res.get('code') == '0':
            details = balance_res['data'][0]['details']
            for asset in details:
                if asset['ccy'] == 'USDT':
                    self.available_usdt = float(asset['availEq'])
                    logger.debug(f"💵 [闲时查账] 当前账户可用 USDT: {self.available_usdt:.2f}")
                    break

    async def set_leverage_on_startup(self):
        """🌟 系统冷启动：1. 切换持仓模式(全/逐)  2. 设置杠杆倍数"""

        # 1. 强制切换保证金模式 (全仓 cross / 逐仓 isolated)
        # 注意：OKX 要求切换模式时不能有持仓或挂单
        mode_payload = {
            "instId": self.symbol,
            "mgnMode": self.td_mode  # 这里传入 "cross"
        }
        logger.info(f"⚙️ [实盘初始化] 正在设置保证金模式为: {self.td_mode}")
        # 虽然接口是 set-isolated-mode，但它其实是用来切换全逐仓的开关
        await self._request("POST", "/api/v5/account/set-isolated-mode", mode_payload)

        # 2. 强制设置杠杆倍数
        lev_payload = {
            "instId": self.symbol,
            "lever": str(self.leverage),
            "mgnMode": self.td_mode
        }
        logger.info(f"⚙️ [实盘初始化] 正在设置杠杆倍数为: {self.leverage}X")
        res = await self._request("POST", "/api/v5/account/set-leverage", lev_payload)

        if res and res.get('code') == '0':
            logger.info(f"✅ 状态同步成功！{self.symbol} 已锁定为 【{self.td_mode.upper()} {self.leverage}X】")
        else:
            logger.warning(f"⚠️ 状态同步返回: {res.get('msg', res)}")
