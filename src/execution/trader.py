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
from src.utils.email_sender import send_trading_signal_email
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

        # 财务官心跳监控
        self._last_balance_update = 0.0  # 最后一次成功查询余额的时间戳
        self._balance_update_failures = 0  # 连续失败次数
        self._max_failures_before_alert = 5  # 连续失败多少次触发警报
        self._alert_sent = False  # 是否已发送警报邮件

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

        # 🌟 参数里加上 sl_side: str

    async def create_stop_loss_order(self, size: float, trigger_price: float, sl_side: str) -> Optional[str]:
        """创建止损订单"""
        try:
            payload = {
                "instId": self.symbol,
                "tdMode": self.td_mode,
                "side": sl_side,  # 🌟 这里改成动态获取的变量
                "ordType": "conditional",
                "sz": str(int(size)),  # 🌟 这里必须加上 int() 防止报小数错误！
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

    async def post_only_sell(self, size: int, price: float) -> Dict:
        """Maker卖出"""
        payload = {
            "instId": self.symbol,
            "tdMode": self.td_mode,
            "side": "sell",
            "ordType": "post_only",
            "sz": str(int(size)),
            "px": str(price),
            "reduceOnly": True
        }
        return await self._request("POST", "/api/v5/trade/order", payload)

    # ==================== 核心交易执行 ====================

    async def update_balance_loop(self):
        """🌟 后台闲时查账协程：每隔 60 秒查询一次余额，缓存在本地

        增强的错误恢复和心跳监控：
        1. 异常捕获：防止单个异常导致循环停止
        2. 指数退避：连续失败时增加等待时间，避免API限流
        3. 心跳时间戳：记录最后成功时间，供外部监控
        4. 失败警报：连续多次失败时发送警报
        """
        logger.info("💰 [财务官] 已上线！将在后台默默监控账户余额...")

        # 🌟 新增：启动时第一件事，先把枪管的威力（杠杆）调好！
        await self.set_leverage_on_startup()

        import time

        while True:
            try:
                # 执行余额查询
                success = await self.fetch_balance()

                # 🌟 核心修复：如果没成功，主动拉响警报！直接丢给下面的 except 块去处理（加倍睡眠+发邮件）！
                if not success:
                    raise ConnectionError("API 请求返回空数据或报错，可能遭遇限流或网络断开")

                # 如果成功了，更新心跳并重置失败计数
                self._last_balance_update = time.time()
                if self._balance_update_failures > 0:
                    logger.info(
                        f"💰 [财务官] 查询恢复成功，重置失败计数（之前连续失败 {self._balance_update_failures} 次）")
                    self._balance_update_failures = 0
                    self._alert_sent = False

                # 检查持仓状态（优先使用context）
                in_position = self.context.is_in_position if self.context else self.is_in_position
                if in_position:
                    # 战时模式：手里有单子，随时可能止盈、止损或打保本
                    # 财务官每 5 秒死死盯住账户，一旦发现单子没了，立刻光速解锁！
                    base_sleep = 5
                else:
                    # 闲时模式：空仓状态，不需要浪费 API 额度，300 秒查一次余额即可
                    base_sleep = 300

                # 应用指数退避（如果有连续失败）
                if self._balance_update_failures > 0:
                    # 指数退避：2^failures * base_sleep，最大不超过300秒（5分钟）
                    backoff_factor = min(2 ** self._balance_update_failures, 60)  # 限制最大60倍
                    sleep_time = min(base_sleep * backoff_factor, 300)
                    logger.warning(f"💰 [财务官] 连续失败 {self._balance_update_failures} 次，"
                                   f"休眠时间延长至 {sleep_time:.1f} 秒")
                    await asyncio.sleep(sleep_time)
                else:
                    await asyncio.sleep(base_sleep)

            except asyncio.CancelledError:
                logger.info("💰 [财务官] 任务被取消")
                raise
            except Exception as e:
                # 捕获所有其他异常，防止循环停止
                self._balance_update_failures += 1
                logger.error(f"💰 [财务官] 第 {self._balance_update_failures} 次查询失败: {e}")

                # 连续失败过多时发送警报
                if self._balance_update_failures >= self._max_failures_before_alert and not self._alert_sent:
                    logger.critical(f"💰 [财务官] 连续失败 {self._balance_update_failures} 次！"
                                    f"可能网络或API出现问题，请立即检查！")

                    # 发送警报邮件
                    try:
                        alert_details = (
                            f"⚠️ 财务官连续 {self._balance_update_failures} 次查询余额失败！\n"
                            f"可能原因：网络连接问题、OKX API限流、或账户权限异常。\n"
                            f"最后成功查询时间: {time.ctime(self._last_balance_update) if self._last_balance_update > 0 else '从未成功'}\n"
                            f"当前状态: {'持仓中' if (self.context.is_in_position if self.context else self.is_in_position) else '空仓'}\n"
                            f"请立即检查服务器网络和OKX API状态！"
                        )
                        await send_trading_signal_email(
                            symbol=self.symbol,
                            signal_type="🚨 财务官心跳异常",
                            price=0.0,
                            details=alert_details
                        )
                        self._alert_sent = True
                        logger.info("📧 财务官异常警报邮件已发送")
                    except Exception as email_error:
                        logger.error(f"❌ 发送警报邮件失败: {email_error}")

                # 失败时使用指数退避等待
                base_sleep = 5 if (self.context.is_in_position if self.context else self.is_in_position) else 300
                backoff_factor = min(2 ** self._balance_update_failures, 60)
                sleep_time = min(base_sleep * backoff_factor, 300)
                logger.warning(f"💰 [财务官] 等待 {sleep_time:.1f} 秒后重试...")
                await asyncio.sleep(sleep_time)

    async def fetch_balance(self) -> bool:
        """请求 OKX 获取 USDT 可用余额。返回获取是否成功。"""
        # 查询当前品种持仓
        pos_res = await self._request("GET", f"/api/v5/account/positions?instId={self.symbol}")
        # 查询当前余额
        balance_res = await self._request("GET", "/api/v5/account/balance")

        # 🌟 核心：如果两个接口任何一个没返回数据，或者报错，都说明网络或API出问题了
        if pos_res is None or balance_res is None or pos_res.get('code') != '0' or balance_res.get('code') != '0':
            return False

        # --- 以下是正常的更新逻辑 (保持不变) ---
        positions = pos_res.get('data', [])
        has_pos = any(abs(float(p.get('pos', 0))) > 0.05 for p in positions)

        if has_pos:
            self.is_in_position = True
            if self.context:
                self.context.is_in_position = True
        else:
            self.is_in_position = False
            if self.context:
                if self.context.is_in_position:
                    logger.warning(f"💰 [财务官] 检测到仓位已消失（可能被手动平仓），清除持仓状态")
                    self.context.clear_position()
                else:
                    self.context.is_in_position = False

        details = balance_res['data'][0]['details']
        for asset in details:
            if asset['ccy'] == 'USDT':
                self.available_usdt = float(asset['availEq'])
                logger.debug(f"💵 [闲时查账] 当前账户可用 USDT: {self.available_usdt:.2f}")
                break

        return True  # 全部成功才返回 True

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
