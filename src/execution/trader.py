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
        self.is_in_position = False  # 默认为空仓

        self.of_wall_price = 0.0  # 三号引擎发现的“隐形筹码墙”价格
        self.of_squeeze_flag = False  # 三号引擎拉响的“极速拉升爆仓”警报

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

    async def execute_snipe(self, price: float, local_low: float, tp2_price: float = None):
        """
        极速三连发执行器：市价开多 -> Maker止盈 -> 条件市价止损
        """
        if self.is_in_position:
            logger.warning("🛡️ [拦截] 手里还有单子没跑完，为了仓位安全，拒绝开第二枪！")
            return

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

        # 🌟 核心修复 1：不要等查账，直接手动标记为持仓中
        self.is_in_position = True

        # ==========================================
        # 3. 分批挂出止盈单 (50%保底 + 50%格局)
        # ==========================================
        tp1_price = round(price * 1.004, 2)
        min_tp2_price = price * 1.008

        if not tp2_price or tp2_price < min_tp2_price:
            tp2_price = round(price * 1.012, 2)
            logger.info(f"🛡️ [风控介入] SMC 阻力位缺失或距离太近，强制将 TP2 目标拔高至 1.2%: {tp2_price}")
        else:
            tp2_price = round(tp2_price, 2)

        tp1_ord_id = None
        tp2_ord_id = None  # 🌟 新增：提前定义 TP2 单号变量

        if sz < 2:
            tp_payload = {
                "instId": self.symbol, "tdMode": self.td_mode, "side": "sell",
                "ordType": "post_only", "sz": str(sz), "px": str(tp1_price), "reduceOnly": True
            }
            logger.info(f"📡 [2/3] 资金不足以分批，单笔止盈单 -> 目标价: {tp1_price}")
            await self._request("POST", "/api/v5/trade/order", tp_payload)
        else:
            sz_half = max(1, int(sz * 0.3))
            sz_rest = sz - sz_half

            tp1_payload = {
                "instId": self.symbol, "tdMode": self.td_mode, "side": "sell",
                "ordType": "post_only", "sz": str(sz_half), "px": str(tp1_price), "reduceOnly": True
            }
            tp2_payload = {
                "instId": self.symbol, "tdMode": self.td_mode, "side": "sell",
                "ordType": "post_only", "sz": str(sz_rest), "px": str(tp2_price), "reduceOnly": True
            }

            logger.info(f"📡 [2/3] 🚀 分批止盈！TP1({sz_half}张): {tp1_price}, TP2({sz_rest}张 结构顶): {tp2_price}")

            # 🌟 工程优化：增加重试机制，对抗交易所仓位延迟
            max_retries = 3
            for attempt in range(max_retries):
                tp_responses = await asyncio.gather(
                    self._request("POST", "/api/v5/trade/order", tp1_payload),
                    self._request("POST", "/api/v5/trade/order", tp2_payload)
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
        sl_price = round(local_low * 0.9985, 2)

        sl_payload = {
            "instId": self.symbol, "tdMode": self.td_mode, "side": "sell",
            "ordType": "conditional", "sz": str(sz), "slTriggerPx": str(sl_price),
            "slTriggerPxType": "last", "slOrdPx": "-1", "reduceOnly": True
        }

        logger.info(f"📡 [3/3] 发送止损单请求 (条件宽幅市价) -> 护城河触发价: {sl_price}")
        res_sl = await self._request("POST", "/api/v5/trade/order-algo", sl_payload)

        sl_algo_id = None  # 🌟 提取原止损单的 ID
        if res_sl and res_sl.get('code') == '0':
            sl_algo_id = res_sl['data'][0]['algoId']
            logger.info(f"✅ 护城河止损单已架设！")
        else:
            logger.error(f"❌ 止损单架设失败: {res_sl}")

        logger.warning("🏁 [三连发完毕] 交易已托管给交易所，等待止盈或止损触发！")

        # 🌟 核心新增：如果仓位够分批，且拿到了双方 ID，立刻启动后台保本护卫 2.0！
        if sz >= 2 and tp1_ord_id and sl_algo_id:
            # 把所有需要的参数统统传给 2.0 护卫
            asyncio.create_task(self._smart_trailing_monitor_v2(
                tp1_ord_id=tp1_ord_id,
                tp2_ord_id=tp2_ord_id,  # 🌟 传给保镖，为了在极端行情下拆除天花板
                sl_algo_id=sl_algo_id,
                entry_price=price,
                tp2_price=tp2_price,  # 🌟 传给保镖，为了计算 0.9% 的吹哨位置
                remaining_sz=sz_rest
            ))

    async def _breakeven_monitor(self, tp1_ord_id, sl_algo_id, entry_price, remaining_sz):
        """🌟 保本护卫 V1.0 (当前已经不用了)：异步轮询 TP1 状态，一旦成交，立刻将止损线上移至保本价"""
        logger.info(f"🛡️ [保本护卫] 已启动！正在静默监视 TP1 订单 ({tp1_ord_id})...")

        # 💡 顶级细节：开仓要 0.05% 的吃单手续费，平仓也要 0.05%。
        # 所以真正的“保本价”不是开盘价，而是开盘价上浮 0.06%，这样连手续费都不会亏！
        breakeven_px = round(entry_price * 1.0006, 2)

        # 循环监控，最多监控 2 个小时 (7200 秒)，防止死循环
        for _ in range(7200):
            await asyncio.sleep(1)  # 每秒查一次，绝不阻塞主线程

            try:
                res = await self._request("GET", f"/api/v5/trade/order?instId={self.symbol}&ordId={tp1_ord_id}")
                if not res or res.get('code') != '0':
                    continue

                state = res['data'][0]['state']

                # 如果发现 TP1 已经成交！
                if state == 'filled':
                    logger.warning(f"🚀 [保本护卫] 侦测到 TP1 已止盈落袋！立即执行保本上移机制...")

                    # 1. 撤销旧的坑底护城河止损
                    cancel_payload = [{"instId": self.symbol, "algoId": sl_algo_id}]
                    await self._request("POST", "/api/v5/trade/cancel-algos", cancel_payload)

                    # 2. 挂出全新的保本止损单
                    new_sl_payload = {
                        "instId": self.symbol,
                        "tdMode": self.td_mode,
                        "side": "sell",
                        "ordType": "conditional",
                        "sz": str(remaining_sz),
                        "slTriggerPx": str(breakeven_px),
                        "slTriggerPxType": "last",
                        "slOrdPx": "-1",  # 触发后市价平仓
                        "reduceOnly": True
                    }
                    res_new_sl = await self._request("POST", "/api/v5/trade/order-algo", new_sl_payload)
                    if res_new_sl and res_new_sl.get('code') == '0':
                        logger.warning(
                            f"✅ [保本护卫] 成功！剩余 {remaining_sz} 张合约的止损线已上移至保本价: {breakeven_px}！这单已立于不败之地！")
                    else:
                        logger.error(f"❌ [保本护卫] 保本止损单架设失败: {res_new_sl}")

                    break  # 任务完成，退出护卫线程

                # 如果 TP1 被手动撤销，或者行情直接暴跌打穿了原止损导致订单失效
                elif state in ['canceled', 'mismatch']:
                    logger.info("🛑 [保本护卫] 侦测到 TP1 订单已被撤销或失效，保本监控结束。")
                    break

            except Exception as e:
                logger.error(f"⚠️ [保本护卫] 监控发生异常: {e}")

    async def _smart_trailing_monitor_v2(self, tp1_ord_id, tp2_ord_id, sl_algo_id, entry_price, tp2_price,
                                         remaining_sz):
        """🌟 保本护卫 2.0：阶梯防守 + 隐形墙跟随 + 无限登月舱"""
        logger.info(f"🛡️ [保本护卫2.0] 已启动！正在静默监视 TP1 ({tp1_ord_id})...")

        # 阶段参数初始化
        breakeven_px = round(entry_price * 1.0006, 2)
        mech_step1_trigger = round(entry_price * 1.008, 2)
        mech_step1_sl = round(entry_price * 1.004, 2)

        # 0.9% 吹哨预警线 (距离 TP2 大约 75% 的位置)
        moonbag_warning_px = round(entry_price + (tp2_price - entry_price) * 0.75, 2)

        current_sl_algo_id = sl_algo_id
        current_sl_px = 0.0  # 记录当前止损到底推到哪里了
        phase = 0  # 0:等保本, 1:锁润与防守, 2:吹哨待命, 3:无限登月

        # 为了防止被 OKX 封锁 API，只有止损线上移超过 0.1% 时，才发送改单请求
        min_move_dist = entry_price * 0.001

        for _ in range(14400):
            await asyncio.sleep(2)  # 极速拉升时，2秒校准一次止损线

            if not self.is_in_position:
                logger.info("🛑 [护卫2.0] 仓位已清空(打止损或吃满)，光荣退役！")
                self.of_wall_price = 0.0
                self.of_squeeze_flag = False
                break

            try:
                # -------------------------------------------------
                # 🟡 阶段二：保本防御 (等 TP1 成交)
                # -------------------------------------------------
                if phase == 0:
                    res = await self._request("GET", f"/api/v5/trade/order?instId={self.symbol}&ordId={tp1_ord_id}")
                    if res and res.get('code') == '0':
                        state = res['data'][0]['state']
                        if state == 'filled':
                            logger.warning(f"🚀 [护卫2.0] TP1 已吃单！【立于不败】止损瞬间上移至保本价: {breakeven_px}")
                            await self._request("POST", "/api/v5/trade/cancel-algos",
                                                [{"instId": self.symbol, "algoId": current_sl_algo_id}])
                            new_sl = {
                                "instId": self.symbol, "tdMode": self.td_mode, "side": "sell",
                                "ordType": "conditional", "sz": str(remaining_sz),
                                "slTriggerPx": str(breakeven_px), "slTriggerPxType": "last", "slOrdPx": "-1",
                                "reduceOnly": True
                            }
                            res_new = await self._request("POST", "/api/v5/trade/order-algo", new_sl)
                            if res_new and res_new.get('code') == '0':
                                current_sl_algo_id = res_new['data'][0]['algoId']
                                current_sl_px = breakeven_px
                                phase = 1  # 进入锁润阶段
                        elif state in ['canceled', 'mismatch']:
                            break
                    await asyncio.sleep(1)

                # -------------------------------------------------
                # 🟠 阶段三 & 🔴 阶段四前夕：阶梯锁润与动能吹哨
                # -------------------------------------------------
                elif phase in [1, 2]:
                    ticker_res = await self._request("GET", f"/api/v5/market/ticker?instId={self.symbol}")
                    if not ticker_res or ticker_res.get('code') != '0':
                        await asyncio.sleep(1)
                        continue

                    last_px = float(ticker_res['data'][0]['last'])
                    target_sl = current_sl_px

                    # 1. 机械阶梯防守
                    if last_px >= mech_step1_trigger:
                        target_sl = max(target_sl, mech_step1_sl)

                    # 2. 🌟 吸收主力肉盾：三号引擎传来的“隐形筹码墙”
                    if self.of_wall_price > entry_price:
                        wall_sl = round(self.of_wall_price * 0.9995, 2)  # 墙下一点点
                        target_sl = max(target_sl, wall_sl)

                    # 发送推止损请求
                    if target_sl > current_sl_px + min_move_dist:
                        logger.warning(f"🧱 [护卫2.0] 防线推进！最新止损锚定至: {target_sl}")
                        await self._request("POST", "/api/v5/trade/cancel-algos",
                                            [{"instId": self.symbol, "algoId": current_sl_algo_id}])
                        new_sl = {
                            "instId": self.symbol, "tdMode": self.td_mode, "side": "sell",
                            "ordType": "conditional", "sz": str(remaining_sz),
                            "slTriggerPx": str(target_sl), "slTriggerPxType": "last", "slOrdPx": "-1",
                            "reduceOnly": True
                        }
                        res_new = await self._request("POST", "/api/v5/trade/order-algo", new_sl)
                        if res_new and res_new.get('code') == '0':
                            current_sl_algo_id = res_new['data'][0]['algoId']
                            current_sl_px = target_sl

                    # 3. 🚨 0.9% 吹哨机制：逼近 TP2，启动雷达！
                    if phase == 1 and last_px >= moonbag_warning_px:
                        logger.warning(f"哨声响起！现价({last_px})已逼近TP2({tp2_price})，进入决断岔路口！")
                        phase = 2

                    # 4. 🚀 决断岔路口：动能破冰！
                    if phase == 2:
                        if self.of_squeeze_flag:
                            logger.warning("🔥 [动能破冰] 三号引擎侦测到空头爆仓踩踏！上方阻力清空！")
                            logger.warning("🛸 [打开天花板] 正在撤销 TP2 止盈单，转入登月舱无限拔高模式！")
                            # 撤销 TP2
                            await self._request("POST", "/api/v5/trade/cancel-order",
                                                {"instId": self.symbol, "ordId": tp2_ord_id})
                            phase = 3  # 正式进入阶段四：无限登月！
                        elif last_px < moonbag_warning_px * 0.998:
                            # 冲高回落，退回阶段 1
                            phase = 1

                    await asyncio.sleep(1)

                # -------------------------------------------------
                # 🔴 阶段四：登月舱 (无限拔高止损)
                # -------------------------------------------------
                elif phase == 3:
                    # 🌟 Zijun 铁律：抛弃1m噪音，拉取最近的 15 根 5m K线！
                    kline_res = await self._request("GET",
                                                    f"/api/v5/market/candles?instId={self.symbol}&bar=5m&limit=15")
                    if kline_res and kline_res.get('code') == '0':
                        klines = kline_res['data']
                        # OKX K线数据结构: [ts, open, high, low, close, vol...] (索引 0 是最新未走完的 K 线)

                        target_sl_moon = current_sl_px

                        # =========================================================
                        # 策略 A：实体 >= 0.2% 强推力阳线 + 确认阳线 (过滤长上影骗炮)
                        # =========================================================
                        # 从索引 2 开始往后找 (因为我们需要 k1(实体K) 和 k2(确认K)，且都必须是已闭合的)
                        for i in range(2, 10):
                            k1_open, k1_low, k1_close = float(klines[i][1]), float(klines[i][3]), float(klines[i][4])
                            k2_open, k2_close = float(klines[i - 1][1]), float(klines[i - 1][4])  # k2 是 k1 之后的一根K线

                            # 1. k1 必须是一根阳线，且实体高度 >= 0.2%
                            k1_body_pct = (k1_close - k1_open) / k1_open
                            if k1_body_pct >= 0.002:
                                # 2. k2 必须也是阳线 (走完行情的确认线)
                                if k2_close > k2_open:
                                    sl_a = k1_low * 0.9995  # 挂在起爆 K 线的最底端
                                    target_sl_moon = max(target_sl_moon, sl_a)
                                    break  # 找到最近的一组就够了

                        # =========================================================
                        # 策略 B：标准的 5m Swing Low 波段低点防守
                        # =========================================================
                        # 寻找标准的 5 根 K 线分型 (中间最低，左右各两根较高)
                        for i in range(3, 10):
                            l0, l1, l2, l3, l4 = [float(klines[j][3]) for j in range(i - 2, i + 3)]
                            if l2 < l0 and l2 < l1 and l2 < l3 and l2 < l4:
                                sl_b = l2 * 0.9995
                                target_sl_moon = max(target_sl_moon, sl_b)
                                break  # 找到最近的一个有效前低

                        # =========================================================
                        # 策略 C：微观横盘吸收的隐形筹码墙防守
                        # =========================================================
                        if self.of_wall_price > 0:
                            target_sl_moon = max(target_sl_moon, self.of_wall_price * 0.9995)

                        # 如果算出的终极天花板止损，比当前高出了 0.1%，立刻拔高！
                        if target_sl_moon > current_sl_px + min_move_dist:
                            logger.warning(f"🚀 [无限登月] 利润狂飙！最新防线极速拔高至: {target_sl_moon:.2f}")
                            await self._request("POST", "/api/v5/trade/cancel-algos",
                                                [{"instId": self.symbol, "algoId": current_sl_algo_id}])
                            new_sl = {
                                "instId": self.symbol, "tdMode": self.td_mode, "side": "sell",
                                "ordType": "conditional", "sz": str(remaining_sz),
                                "slTriggerPx": str(target_sl_moon), "slTriggerPxType": "last", "slOrdPx": "-1",
                                "reduceOnly": True
                            }
                            res_new = await self._request("POST", "/api/v5/trade/order-algo", new_sl)
                            if res_new and res_new.get('code') == '0':
                                current_sl_algo_id = res_new['data'][0]['algoId']
                                current_sl_px = target_sl_moon

                    # 无限登月状态下，每 5（3+2）秒探测一次 K 线形态就足够了 (降低 API 频率)
                    await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"⚠️ [护卫2.0] 异常: {e}")
                await asyncio.sleep(2)

    async def update_balance_loop(self):
        """🌟 后台闲时查账协程：每隔 60 秒查询一次余额，缓存在本地"""
        logger.info("💰 [财务官] 已上线！将在后台默默监控账户余额...")

        # 🌟 新增：启动时第一件事，先把枪管的威力（杠杆）调好！
        await self.set_leverage_on_startup()

        while True:
            await self.fetch_balance()
            if self.is_in_position:
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
            else:
                # 只有当我们本身没有强制持仓时，才设为 False
                self.is_in_position = False

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
