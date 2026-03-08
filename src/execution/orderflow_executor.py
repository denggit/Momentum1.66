#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OrderFlow执行策略器
封装三号引擎特有的"三连发"执行逻辑，使OKXTrader保持为纯API层
"""
import asyncio
from dataclasses import dataclass
from typing import Optional

from src.execution.trader import OKXTrader, ExecutionResult
from src.strategy.orderflow_config import OrderFlowConfig
from src.utils.log import get_logger

logger = get_logger(__name__)


@dataclass
class OrderFlowExecutionConfig:
    """OrderFlow执行策略配置"""
    tp1_split_ratio: float = 0.5  # TP1仓位分割比例 (30%去TP1，70%去TP2)
    tp1_min_size: int = 1  # TP1最小张数


class OrderFlowExecutor:
    """OrderFlow三连发执行策略器"""

    def __init__(self, trader: OKXTrader, config: OrderFlowConfig):
        """
        初始化执行策略器

        Args:
            trader: OKXTrader实例（纯API层）
            config: OrderFlowConfig配置
        """
        self.trader = trader
        self.config = config

        # 执行策略配置（可以从config中扩展）
        self.exec_config = OrderFlowExecutionConfig(
            tp1_split_ratio=config.tp1_split_ratio,
            tp1_min_size=config.tp1_min_size
        )

    async def execute_snipe(self, price: float, local_low: float, tp2_price: float = None) -> Optional[ExecutionResult]:
        """
        极速三连发执行器：市价开多 -> Maker止盈 -> 条件市价止损
        """
        # 检查是否已有持仓（优先使用context，保持向后兼容）
        has_position = self.trader.context.is_in_position if self.trader.context else self.trader.is_in_position
        if has_position:
            logger.warning("🛡️ [拦截] 手里还有单子没跑完，为了仓位安全，拒绝开第二枪！")
            return None

        if not self.trader.api_key:
            logger.error("❌ 实盘 API 未配置，拒绝下单。")
            return None

        # 🌟 动态计算本次应下注的本金
        if self.trader.available_usdt <= 0:
            logger.error("❌ 本地缓存余额不足或未获取到余额，放弃本次开火！")
            return None

        risk_usdt = self.trader.available_usdt * self.trader.risk_pct

        # ==========================================
        # 1. 计算仓位大小 (合约张数)
        # ==========================================
        ct_val = self.trader.ct_val_map.get(self.trader.symbol, 1.0)

        # 实际名义价值 = 动用资金 * 杠杆倍数
        notional_value = risk_usdt * self.trader.leverage
        # 购买张数 = 名义价值 / 现价 / 每张面值
        sz = int(notional_value / price / ct_val)

        if sz <= 0:
            logger.error(f"⚠️ 计算出的仓位太小 (张数: {sz})，无法下单！检查你的 risk_usdt。")
            return None

        logger.warning(f"🔫 [实盘执行] 正在扣动扳机！目标张数: {sz} 张 (名义价值: ${notional_value:.2f})")

        # ==========================================
        # 2. 市价吃单 (Taker Buy)
        # ==========================================
        order_payload = {
            "instId": self.trader.symbol,
            "tdMode": self.trader.td_mode,
            "side": "buy",
            "ordType": "market",
            "sz": str(sz)
        }

        logger.info("📡 [1/3] 发送市价开仓请求...")
        res_buy = await self.trader._request("POST", "/api/v5/trade/order", order_payload)

        if not res_buy or res_buy.get('code') != '0':
            logger.error(f"❌ 市价买入失败: {res_buy}")
            return None

        logger.info(f"✅ 市价开多成功！订单号: {res_buy['data'][0]['ordId']}")

        # 稍微等 100ms 确保仓位已经结算到账户，防止 reduceOnly 报错
        await asyncio.sleep(0.1)

        # 🌟 核心修复 1：不要等查账，直接手动标记为持仓中
        self.trader.is_in_position = True
        if self.trader.context:
            # 更新MarketContext中的持仓状态
            self.trader.context.is_in_position = True

        # ==========================================
        # 3. 分批挂出止盈单 (30%保底 + 70%格局)
        # ==========================================
        tp1_price = round(price * (1 + self.config.tp1_pct), 2)
        min_tp2_price = price * (1 + self.config.tp1_pct * 2)  # TP2至少比TP1高一个TP1的幅度

        if not tp2_price or tp2_price < min_tp2_price:
            tp2_price = round(price * (1 + self.config.tp2_pct), 2)
            logger.info(
                f"🛡️ [风控介入] SMC 阻力位缺失或距离太近，强制将 TP2 目标拔高至 {self.config.tp2_pct * 100:.1f}%: {tp2_price}")
        else:
            tp2_price = round(tp2_price, 2)

        tp1_ord_id = None
        tp2_ord_id = None

        if sz < 2:
            # 仓位太小，无法分批，全部挂到TP1
            tp_payload = {
                "instId": self.trader.symbol, "tdMode": self.trader.td_mode, "side": "sell",
                "ordType": "post_only", "sz": str(sz), "px": str(tp1_price), "reduceOnly": True
            }
            logger.info(f"📡 [2/3] 资金不足以分批，单笔止盈单 -> 目标价: {tp1_price}")
            res_tp = await self.trader._request("POST", "/api/v5/trade/order", tp_payload)
            if res_tp and res_tp.get('code') == '0':
                tp1_ord_id = res_tp['data'][0]['ordId']
            sz_rest = 0  # 没有剩余仓位
        else:
            # 正常分批：根据比例分配TP1和TP2仓位
            sz_half = max(self.exec_config.tp1_min_size, int(sz * self.exec_config.tp1_split_ratio))
            sz_rest = sz - sz_half

            tp1_payload = {
                "instId": self.trader.symbol, "tdMode": self.trader.td_mode, "side": "sell",
                "ordType": "post_only", "sz": str(sz_half), "px": str(tp1_price), "reduceOnly": True
            }

            # 如果没有TP2仓位（sz_rest <= 0），则只下TP1单
            if sz_rest <= 0:
                logger.info(f"📡 [2/3] 无TP2仓位，全部仓位挂到TP1: {sz_half}张, 目标价: {tp1_price}")

                max_retries = 3
                for attempt in range(max_retries):
                    res_tp1 = await self.trader._request("POST", "/api/v5/trade/order", tp1_payload)
                    if res_tp1 and res_tp1.get('code') == '0':
                        tp1_ord_id = res_tp1['data'][0]['ordId']
                        break
                    logger.warning(f"⚠️ 挂TP1单遇到延迟 (尝试 {attempt + 1}/{max_retries})，0.2秒后重试...")
                    await asyncio.sleep(0.2)
            else:
                # 有TP2仓位，正常下两个单
                tp2_payload = {
                    "instId": self.trader.symbol, "tdMode": self.trader.td_mode, "side": "sell",
                    "ordType": "post_only", "sz": str(sz_rest), "px": str(tp2_price), "reduceOnly": True
                }

                logger.info(f"📡 [2/3] 🚀 分批止盈！TP1({sz_half}张): {tp1_price}, TP2({sz_rest}张 结构顶): {tp2_price}")

                # 🌟 工程优化：增加重试机制，对抗交易所仓位延迟
                max_retries = 3
                for attempt in range(max_retries):
                    tp_responses = await asyncio.gather(
                        self.trader._request("POST", "/api/v5/trade/order", tp1_payload),
                        self.trader._request("POST", "/api/v5/trade/order", tp2_payload)
                    )
                    res_tp1, res_tp2 = tp_responses[0], tp_responses[1]

                    if res_tp1 and res_tp1.get('code') == '0':
                        tp1_ord_id = res_tp1['data'][0]['ordId']
                    if res_tp2 and res_tp2.get('code') == '0':
                        tp2_ord_id = res_tp2['data'][0]['ordId']

                    # 如果两个单号都拿到了，完美退出重试
                    if tp1_ord_id and tp2_ord_id:
                        break

                    # 否则说明碰到了 reduceOnly 报错，再等 0.2 秒重试！
                    logger.warning(f"⚠️ 挂止盈单遇到延迟 (尝试 {attempt + 1}/{max_retries})，0.2秒后重试...")
                    await asyncio.sleep(0.2)

        # ==========================================
        # 4. 挂出条件止损单 (Conditional Market Sell)
        # ==========================================
        sl_price = round(local_low * (1 - self.config.sl_pct), 2)

        sl_payload = {
            "instId": self.trader.symbol, "tdMode": self.trader.td_mode, "side": "sell",
            "ordType": "conditional", "sz": str(sz), "slTriggerPx": str(sl_price),
            "slTriggerPxType": "last", "slOrdPx": "-1", "reduceOnly": True
        }

        logger.info(f"📡 [3/3] 发送止损单请求 (条件宽幅市价) -> 护城河触发价: {sl_price}")
        res_sl = await self.trader._request("POST", "/api/v5/trade/order-algo", sl_payload)

        sl_algo_id = None
        if res_sl and res_sl.get('code') == '0':
            sl_algo_id = res_sl['data'][0]['algoId']
            logger.info(f"✅ 护城河止损单已架设！")
        else:
            logger.error(f"❌ 止损单架设失败: {res_sl}")

        logger.warning("🏁 [三连发完毕] 交易已托管给交易所，等待止盈或止损触发！")

        # 创建执行结果对象
        execution_result = ExecutionResult(
            symbol=self.trader.symbol,
            entry_price=price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            local_low=local_low,
            position_size=sz,
            tp1_order_id=tp1_ord_id,
            tp2_order_id=tp2_ord_id,
            sl_algo_id=sl_algo_id,
            remaining_size=sz_rest if sz >= 2 else None
        )

        return execution_result
