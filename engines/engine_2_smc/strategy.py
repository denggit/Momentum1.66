#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/2/26 11:26 PM
@File       : strategy.py
@Description: 
"""
import asyncio
import datetime
import os
import sys

import numpy as np
import pandas as pd

# 确保能导入 src 目录下的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_loader import OKXDataLoader
from src.utils.volume_profile import CompositeVolumeProfile
from src.utils.log import get_logger

logger = get_logger(__name__)


class MicroSMCRadar:
    def __init__(self, symbol="ETH-USDT-SWAP", timeframe="5m"):
        self.symbol = symbol
        self.timeframe = timeframe
        # 直接复用你极其强大的 okx_loader 来拉取 5分钟 K线
        self.loader = OKXDataLoader(symbol=symbol, timeframe=timeframe)

        # 存储计算出的兴趣区 (Point of Interest)
        self.active_pois = []
        self.macro_vp_metrics = None  # 🌟 新增：存放 48 小时的全局地形数据

    def update_structure(self):
        """定期拉取最新 K 线，重新绘制 5m SMC 支撑区，并扫描全局筹码"""
        try:
            # 🌟 视野放大到 600 根 (48小时)，寻找未消耗的结构和全局筹码
            df = self.loader.fetch_historical_data(limit=600).copy()
            if df is None or df.empty or len(df) < 5:
                logger.warning("⚠️ [SMC雷达] 拉取数据为空，跳过本次更新。")
                return

            # 1. 计算未失效的 SMC 支撑区 (你刚才已经加了 is_mitigated 的代码，这里复用)
            self.active_pois = self._calculate_support_pois(df)

            # 2. 🌟 附加任务：计算这 48 小时的全局筹码分布！
            vp_analyzer = CompositeVolumeProfile()
            self.macro_vp_metrics = vp_analyzer.analyze_macro_profile(df)

            hvn_count = len(self.macro_vp_metrics['hvns']) if self.macro_vp_metrics else 0
            logger.debug(f"🗺️ [SMC雷达] 地图更新！发现 {len(self.active_pois)} 个有效POI，探明 {hvn_count} 座宏观筹码峰。")

        except Exception as e:
            logger.exception(f"❌ [SMC雷达] 更新K线结构失败: {e}")

    # ====================================================
    # 🌟 用这个全新的宏观交叉验证，替换掉旧的 auto_verify_volume_support
    # ====================================================
    def verify_with_macro_vp(self, smc_price: float):
        """
        全自动筹码测谎仪：判断现价落在山峰 (HVN) 还是山谷 (LVN)？
        """
        if not self.macro_vp_metrics:
            return True, "筹码地形未知"  # 如果地形没算出来，放行 (容错处理)

        hvns = np.array(self.macro_vp_metrics['hvns'])
        lvns = np.array(self.macro_vp_metrics['lvns'])

        if len(hvns) == 0 or len(lvns) == 0:
            return True, "筹码分布无明显峰谷"

        # 1. 计算距离最近的山峰和山谷
        dist_to_nearest_hvn = np.min(np.abs(hvns - smc_price))
        dist_to_nearest_lvn = np.min(np.abs(lvns - smc_price))

        # 2. 一票否决：如果紧挨着 LVN (真空区，比如距离 < 1 美金)，直接拦截！
        if dist_to_nearest_lvn <= 1.0:
            return False, "⚠️ 处于 LVN 真空区，支撑极度脆弱"

        # 3. 完美共振：如果紧挨着 HVN (比如距离 < 2.5 美金)
        if dist_to_nearest_hvn <= 2.5:
            return True, "✅ SMC 结构与宏观 HVN 筹码峰完美共振"

        # 4. 如果都在中间，属于普通支撑
        return True, "✅ 普通支撑区(未见筹码真空)"

    def final_check(self, price):
        """指挥部专用的最终审核接口"""
        # 第一关：有没有结构？
        is_poi, msg1 = self.is_in_poi(price)
        if not is_poi:
            return False, msg1

        # 第二关：🌟 宏观筹码交叉验证
        is_vol_safe, msg2 = self.verify_with_macro_vp(price)
        if not is_vol_safe:
            return False, f"结构虽在，但 {msg2}"

        return True, f"{msg1} | {msg2}"

    def _calculate_support_pois(self, df: pd.DataFrame) -> list:
        """
        寻找未被消耗的 (Unmitigated) 极值订单块、波段低点和顶底转换区
        并自动过滤诱导陷阱 (Inducement)
        """
        pois = []
        current_price = df['close'].iloc[-1]

        if 'ATR' not in df.columns:
            df['ATR'] = (df['high'] - df['low']).rolling(window=14).mean().fillna(current_price * 0.002)

        current_atr = df['ATR'].iloc[-1]
        cluster_threshold = 1.5 * current_atr
        sweep_allowance = current_price * 0.0015
        top_allowance = current_price * 0.0005

        def is_inducement(test_bottom, existing_pois):
            for ep in existing_pois:
                distance = abs(test_bottom - ep['bottom'])
                if distance < cluster_threshold and test_bottom > ep['bottom']:
                    return True
            return False

        # 🌟 核心新增：检测支撑位是否在形成后被砸穿过 (Mitigation Check)
        def is_mitigated(poi_bottom: float, formation_idx: int) -> bool:
            """
            如果在 POI 形成之后的任何时刻，最低价曾经跌破过它的底部，
            则该 POI 已被消耗 (Mitigated) 彻底失效。
            """
            # 取出从这个支撑位形成之后的全部 K 线
            future_k_lines = df['low'].iloc[formation_idx + 1:]
            if future_k_lines.empty:
                return False
            # 如果这期间的最低价跌破了支撑底部，判定为失效
            return future_k_lines.min() < poi_bottom

        # ==================================================
        # 1. 寻找“未失效”的极值订单块 (Order Block)
        # ==================================================
        for i in range(len(df) - 5, 5, -1):
            if df['close'].iloc[i] < df['open'].iloc[i]:
                ob_height = df['high'].iloc[i] - df['low'].iloc[i]
                future_move = df['close'].iloc[i + 1:i + 4].max() - df['close'].iloc[i]

                is_strong_move = future_move > (1.5 * df['ATR'].iloc[i])
                is_valid_structure = ob_height < future_move
                is_not_too_thick = ob_height < (2.5 * df['ATR'].iloc[i])

                if is_strong_move and is_valid_structure and is_not_too_thick:
                    ob_top = df['open'].iloc[i]
                    ob_bottom = df['low'].iloc[i]

                    if ob_top < current_price:
                        # 🌟 检查这个 OB 是不是早就被砸穿了
                        if not is_mitigated(ob_bottom, i):
                            pois.append({
                                'type': 'Order_Block',
                                'top': ob_top,
                                'bottom': ob_bottom,
                                'time': df.index[i]
                            })
                            break  # 找到最近且【未失效】的 1 个即可
                        else:
                            logger.debug(f"🧹 [SMC雷达] 发现订单块，但已被砸穿失效，继续向历史深处寻找！")

        # ==================================================
        # 2. 寻找“未失效”的波段低点 (Swing Low)
        # ==================================================
        df['is_swing_low'] = ((df['low'] < df['low'].shift(1)) & (df['low'] < df['low'].shift(2)) &
                              (df['low'] < df['low'].shift(-1)) & (df['low'] < df['low'].shift(-2)))

        valid_sls = 0
        # 使用索引倒序遍历，方便获取 formation_idx
        for i in range(len(df) - 1, 0, -1):
            if valid_sls >= 4: break

            if df['is_swing_low'].iloc[i] and df['low'].iloc[i] < current_price:
                sl_bottom = df['low'].iloc[i] - sweep_allowance

                # 🌟 如果这个前低后来被更低的暴跌刺穿了，跳过它！
                if is_mitigated(sl_bottom, i):
                    continue

                if not is_inducement(sl_bottom, pois):
                    pois.append({
                        'type': 'Swing_Low',
                        'top': df['low'].iloc[i] + top_allowance,
                        'bottom': sl_bottom,
                        'time': df.index[i]
                    })
                    valid_sls += 1

        # ==================================================
        # 3. 寻找“未失效”的顶底转换区 (Broken Swing High)
        # ==================================================
        df['is_swing_high'] = ((df['high'] > df['high'].shift(1)) & (df['high'] > df['high'].shift(2)) &
                               (df['high'] > df['high'].shift(-1)) & (df['high'] > df['high'].shift(-2)))

        tolerance_buffer = current_price * 0.003
        valid_bsh = 0

        for i in range(len(df) - 1, 0, -1):
            if valid_bsh >= 4: break

            if df['is_swing_high'].iloc[i] and df['high'].iloc[i] < current_price + tolerance_buffer:
                bsh_bottom = df['high'].iloc[i] - sweep_allowance

                # 🌟 如果这个前高被突破后，又被一次深蹲彻底砸回去了，跳过它！
                if is_mitigated(bsh_bottom, i):
                    continue

                if not is_inducement(bsh_bottom, pois):
                    pois.append({
                        'type': 'Broken_Swing_High',
                        'top': df['high'].iloc[i] + top_allowance,
                        'bottom': bsh_bottom,
                        'time': df.index[i]
                    })
                    valid_bsh += 1

        return pois

    def is_in_poi(self, price: float) -> tuple[bool, str]:
        """
        三号引擎调用接口：传入当前价格，问雷达兵“这里能开火吗？”
        返回 (True/False, 区域描述)
        """
        if not self.active_pois:
            return False, "无结构"

        for poi in self.active_pois:
            if poi['bottom'] <= price <= poi['top']:
                return True, f"命中 {poi['type']} 支撑区 ({poi['bottom']:.1f} ~ {poi['top']:.1f})"

        return False, "悬空"

    def get_nearest_resistance(self, current_price: float):
        """🌟 进阶版：寻找上方最近的阻力位 (Bearish OB 或 实体 Swing High)"""
        try:
            df = self.loader.fetch_historical_data(limit=300)
            if df is None or df.empty:
                return None

            resistances = []

            # 1. 寻找看空订单块 (Bearish OB: 暴跌前的最后一根阳线)
            for i in range(len(df) - 5, 5, -1):
                # 寻找阳线
                if df['close'].iloc[i] > df['open'].iloc[i]:
                    ob_height = df['high'].iloc[i] - df['low'].iloc[i]
                    # 随后是否出现了强力砸盘？
                    future_drop = df['close'].iloc[i] - df['close'].iloc[i + 1:i + 4].min()

                    # 使用 1.5倍 ATR 确认跌幅动能
                    atr = df['ATR'].iloc[i] if 'ATR' in df.columns else (current_price * 0.002)
                    is_strong_drop = future_drop > (1.5 * atr)

                    if is_strong_drop:
                        # 🌟 核心：挂在看空订单块的【实体底部(Open)】，绝不去碰上边缘
                        ob_bottom = df['open'].iloc[i]
                        if ob_bottom > current_price:
                            resistances.append({'type': 'Bearish_OB', 'price': ob_bottom})
                            break  # 找到最近的一个就够了

            # 2. 寻找波段高点 (Swing High)
            df['is_swing_high'] = ((df['high'] > df['high'].shift(1)) & (df['high'] > df['high'].shift(2)) &
                                   (df['high'] > df['high'].shift(-1)) & (df['high'] > df['high'].shift(-2)))

            swing_highs = df[(df['is_swing_high'] == True) & (df['high'] > current_price)]

            if not swing_highs.empty:
                # 获取最近的一个波段高点 K 线
                sh_row = swing_highs.iloc[-1]

                # 🌟 核心修改：绝对不取最高点针尖 (high)！
                # 取开盘价和收盘价中的较高者 (实体顶部)，这叫 "Front-running the liquidity" (抢跑流动性)
                body_top = max(sh_row['open'], sh_row['close'])
                resistances.append({'type': 'Swing_High', 'price': body_top})

            # 如果都没找到
            if not resistances:
                return None

            # 3. 找出距离现价最近的那个阻力位
            resistances.sort(key=lambda x: x['price'])
            nearest = resistances[0]

            logger.debug(f"🎯 [SMC雷达] 锁定上方阻力 ({nearest['type']}): 目标价 {nearest['price']:.2f}")
            return nearest['price']

        except Exception as e:
            logger.error(f"❌ [SMC雷达] 寻找阻力位失败: {e}")

        return None

    # 在 MicroSMCRadar 类中新增这个函数：
    async def background_update_loop(self):
        """🌟 后台静默守护进程：自动解析 timeframe，极其精确地对齐换线瞬间"""
        logger.info(f"📡 [SMC雷达] 已启动静默扫描！将精确对齐 {self.timeframe} 周期换线瞬间抓取 K 线...")

        # 解析当前的 timeframe 是多少分钟
        tf_minutes = 5
        if self.timeframe.endswith('m'):
            tf_minutes = int(self.timeframe.replace('m', ''))
        elif self.timeframe.endswith('H'):
            tf_minutes = int(self.timeframe.replace('H', '')) * 60

        # 🌟 核心修复：系统刚启动时，直接强制拉取一次，防止在等待下个 00 秒期间“瞎眼”
        logger.info("📡 [SMC雷达] 正在执行冷启动初次侦察...")
        await asyncio.to_thread(self.update_structure)

        while True:
            now = datetime.datetime.now()

            # 计算当前时刻的“绝对秒数”
            current_seconds = now.minute * 60 + now.second + (now.microsecond / 1_000_000.0)

            # 计算下一个 K 线周期的“绝对秒数” (例如现在是 32 分，下一个 5m 周期是 35 分)
            target_seconds = ((now.minute // tf_minutes) + 1) * tf_minutes * 60

            # 算出还需要休眠多少秒
            sleep_seconds = target_seconds - current_seconds

            # 防抖动保护：万一算出来是负数或极小值，说明刚刚跨过整点
            if sleep_seconds <= 0.1:
                sleep_seconds += tf_minutes * 60

            logger.debug(f"⏳ [SMC雷达] 正在休眠等待换线，距离下一次拉取还有 {sleep_seconds:.1f} 秒...")
            await asyncio.sleep(sleep_seconds)

            # 换线瞬间！立刻用异步线程池去拉取最新闭合的 K 线
            await asyncio.to_thread(self.update_structure)


if __name__ == "__main__":
    # 简单的本地测试，看看雷达兵能不能正常画出地图
    radar = MicroSMCRadar(symbol="ETH-USDT-SWAP", timeframe="5m")
    radar.update_structure()
    print("\n🗺️ 当前算出的 5m 支撑防线：")
    for p in radar.active_pois:
        print(f"[{p['type']}] 顶部: {p['top']}, 底部: {p['bottom']}, 生成时间: {p['time']}")

    test_price = 2050.0
    is_safe, msg = radar.is_in_poi(test_price)
    print(f"\n现价 {test_price} 能否抄底？ -> {is_safe} ({msg})")
