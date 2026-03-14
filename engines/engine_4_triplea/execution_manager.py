#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TripleA 执行管理器 (Execution Manager)
对齐三号引擎执行逻辑：市价开仓 -> 真实限价Maker止盈 -> 条件市价止损
充分利用 reduceOnly 机制实现伪 OCO，最大化节省止盈手续费。
"""
import asyncio
from typing import Dict

from src.execution.trader import OKXTrader
from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleAExecutionManager:
    def __init__(self, trader: OKXTrader):
        self.trader = trader

    async def execute_signal(self, signal: Dict) -> bool:
        if not self.trader.api_key:
            logger.error("❌ 实盘 API 未配置，拒绝下单。")
            return False

        if self.trader.available_usdt <= 0:
            logger.error("❌ 本地可用余额不足，放弃开火！")
            return False

        action = signal['action']
        entry_price = signal['entry_price']
        tp_price = signal['take_profit']
        sl_price = signal['stop_loss']

        # 1. 计算仓位大小 (张数)
        risk_usdt = self.trader.available_usdt * self.trader.risk_pct
        notional_value = risk_usdt * self.trader.leverage
        ct_val = self.trader.ct_val_map.get(self.trader.symbol, 1.0)
        sz = int(notional_value / entry_price / ct_val)

        if sz <= 0:
            logger.error(f"⚠️ 仓位太小 (张数: {sz})，无法下单。")
            return False

        logger.info(f"🔫 [实盘执行] 目标张数: {sz} 张 | 方向: {action}")

        # ==========================================
        # 第 1 枪：市价极速吃单 (Taker Open)
        # ==========================================
        open_side = "buy" if action == "BUY" else "sell"
        order_payload = {
            "instId": self.trader.symbol,
            "tdMode": self.trader.td_mode,
            "side": open_side,
            "ordType": "market",
            "sz": str(sz)
        }

        logger.info("📡 [1/3] 发送市价开仓请求...")
        res_open = await self.trader._request("POST", "/api/v5/trade/order", order_payload)

        if not res_open or res_open.get('code') != '0':
            logger.error(f"❌ 市价开仓失败: {res_open}")
            return False

        logger.info(f"✅ 市价开仓成功！订单号: {res_open['data'][0]['ordId']}")

        # 微秒级休眠，确保交易所底层账本已更新仓位，防止下方的 reduceOnly 报错
        await asyncio.sleep(0.1)
        self.trader.is_in_position = True

        # ==========================================
        # 第 2 枪：真实限价挂单止盈 (Maker TP + reduceOnly)
        # ==========================================
        close_side = "sell" if action == "BUY" else "buy"
        tp_payload = {
            "instId": self.trader.symbol,
            "tdMode": self.trader.td_mode,
            "side": close_side,
            "ordType": "post_only",  # 严格保证 Maker
            "sz": str(sz),
            "px": str(tp_price),
            "reduceOnly": True  # 核心机制：只减仓
        }

        logger.info(f"📡 [2/3] 架设 Maker 止盈网 -> 目标价: {tp_price}")
        res_tp = await self.trader._request("POST", "/api/v5/trade/order", tp_payload)

        if not res_tp or res_tp.get('code') != '0':
            # post_only 如果直接击穿盘口会被取消，需考虑回退机制，但通常突破策略的TP较远，极少发生
            logger.warning(f"⚠️ Maker 止盈挂单失败或被弹回: {res_tp}")

        # ==========================================
        # 第 3 枪：条件市价止损 (Conditional SL + reduceOnly)
        # ==========================================
        sl_payload = {
            "instId": self.trader.symbol,
            "tdMode": self.trader.td_mode,
            "side": close_side,
            "ordType": "conditional",
            "sz": str(sz),
            "slTriggerPx": str(sl_price),
            "slTriggerPxType": "last",
            "slOrdPx": "-1",  # 触发后市价砸盘逃生
            "reduceOnly": True  # 核心机制：只减仓
        }

        logger.info(f"📡 [3/3] 部署条件止损底线 -> 触发价: {sl_price}")
        res_sl = await self.trader._request("POST", "/api/v5/trade/order-algo", sl_payload)

        if res_sl and res_sl.get('code') == '0':
            logger.info(f"✅ 止损防线已就绪！")
        else:
            logger.error(f"❌ 止损单部署失败: {res_sl}")

        logger.warning("🏁 [三连发完毕] 阵地已布好。引擎重置。")
        return True
