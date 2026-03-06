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
    def __init__(self, symbol="ETH-USDT-SWAP", timeframes=None):
        self.symbol = symbol
        # 🌟 核心：默认同时扫描 3 个级别！
        self.timeframes = timeframes or ["5m", "15m", "1H"]
        self.tf_limit_mapping = {"5m": 600, "15m": 200, "1H": 168}
        
        # 为每个时间级别实例化一个专用的数据加载器
        self.loaders = {tf: OKXDataLoader(symbol=symbol, timeframe=tf) for tf in self.timeframes}

        # 存储计算出的兴趣区 (Point of Interest)
        self.active_pois = []
        self.macro_vp_metrics = None  # 🌟 新增：存放 48 小时的全局地形数据

    def update_structure(self):
        """定期拉取多级别 K 线，合并 SMC 支撑区，并扫描 5m 全局筹码"""
        try:
            all_pois = []
            # 1. 🌟 遍历多时间级别，绘制复合地图
            for tf in self.timeframes:
                limit = self.tf_limit_mapping.get(tf, 600)
                df = self.loaders[tf].fetch_historical_data(limit=limit)
                
                if df is not None and not df.empty and len(df) >= 5:
                    df = df.copy()
                    tf_pois = self._calculate_support_pois(df, tf_label=tf)
                    all_pois.extend(tf_pois)
            
            self.active_pois = all_pois

            # 2. 🌟 筹码测绘：永远使用 5m 的高清数据来扫描地形
            df_5m = self.loaders["5m"].fetch_historical_data(limit=600)
            if df_5m is not None and not df_5m.empty:
                vp_analyzer = CompositeVolumeProfile()
                self.macro_vp_metrics = vp_analyzer.analyze_macro_profile(df_5m.copy())

            hvn_count = len(self.macro_vp_metrics['hvns']) if self.macro_vp_metrics else 0
            logger.debug(f"🗺️ [MTF雷达] 多维结构更新！共 {len(self.active_pois)} 个复合POI，探明 {hvn_count} 座筹码峰。")

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

    def _calculate_support_pois(self, df: pd.DataFrame, tf_label: str = "5m") -> list:
        """
        寻找未被消耗的极值订单块、波段低点和顶底转换区
        加入 1.5% 显著度过滤、OB 吞没过滤 和 FVG 动态真空识别！
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

        def is_mitigated(poi_bottom: float, formation_idx: int) -> bool:
            future_k_lines = df['low'].iloc[formation_idx + 1:]
            if future_k_lines.empty:
                return False
            return future_k_lines.min() < poi_bottom

        # ==================================================
        # 1. 寻找“未失效且强势”的极值订单块 (Colorless Order Block)
        # ==================================================
        valid_obs = 0
        for i in range(len(df) - 5, 5, -1):
            if valid_obs >= 4: break

            # 🌟 Zijun 的终极领悟：无视 K 线颜色！只看实体大小和随后的能量爆发！
            ob_body = abs(df['open'].iloc[i] - df['close'].iloc[i])
            ob_height = df['high'].iloc[i] - df['low'].iloc[i]

            # 提取下一根 K 线，验证多头动能爆发 (Displacement)
            next_candle = df.iloc[i + 1]
            next_body = next_candle['close'] - next_candle['open']

            # 1. 爆发必须是强力阳线 (next_body > 0)
            # 2. 吞没验证：爆发阳线的实体，必须无情吞没这根“蓄力K线”的实体！
            if next_body <= 0 or next_body <= ob_body:
                continue  # 毫不留情跳过！

            # 测量后续 3 根 K 线的总推力
            future_move = df['close'].iloc[i + 1:i + 4].max() - df['low'].iloc[i]

            is_strong_move = future_move > (1.5 * df['ATR'].iloc[i])
            is_valid_structure = ob_height < future_move
            is_not_too_thick = ob_height < (2.5 * df['ATR'].iloc[i])

            if is_strong_move and is_valid_structure and is_not_too_thick:
                # 无论这根蓄力 K 线是阴是阳，它的实体顶部 (Max) 就是阻力墙，下影线就是防守底线！
                ob_top = max(df['open'].iloc[i], df['close'].iloc[i])
                ob_bottom = df['low'].iloc[i]

                if ob_top < current_price:
                    if not is_mitigated(ob_bottom, i):
                        pois.append({
                            'type': f'{tf_label}_Order_Block',
                            'top': ob_top,
                            'bottom': ob_bottom,
                            'time': df.index[i]
                        })
                        valid_obs += 1

        # ==================================================
        # 2. 寻找“未失效且显著”的波段低点 (Swing Low)
        # ==================================================
        df['is_swing_low'] = ((df['low'] < df['low'].shift(1)) & (df['low'] < df['low'].shift(2)) &
                              (df['low'] < df['low'].shift(-1)) & (df['low'] < df['low'].shift(-2)))

        valid_sls = 0
        for i in range(len(df) - 3, 2, -1):
            if valid_sls >= 4: break

            if df['is_swing_low'].iloc[i] and df['low'].iloc[i] < current_price:
                sl_bottom = df['low'].iloc[i]

                # 🌟 Zijun 的铁律：1.5% 右侧反弹显著度过滤 (Prominence Check)
                future_highs = df['high'].iloc[i + 1:]
                if not future_highs.empty:
                    recent_bounce_high = future_highs.max()
                    bounce_pct = (recent_bounce_high - sl_bottom) / sl_bottom

                    # 如果这波右侧反弹连 1.5% 的空间都没打出来，说明是诱多假底！跳过！
                    if bounce_pct < 0.015:
                        continue
                else:
                    continue

                sl_bottom = sl_bottom - sweep_allowance

                if is_mitigated(sl_bottom, i):
                    continue

                if not is_inducement(sl_bottom, pois):
                    pois.append({
                        'type': f'{tf_label}_Swing_Low',
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
                if is_mitigated(bsh_bottom, i): continue
                if not is_inducement(bsh_bottom, pois):
                    pois.append({
                        'type': f'{tf_label}_Broken_Swing_High',
                        'top': df['high'].iloc[i] + top_allowance,
                        'bottom': bsh_bottom,
                        'time': df.index[i]
                    })
                    valid_bsh += 1

        # ==================================================
        # 🌟 4. 新增：寻找未被完全填补的 FVG (流动性真空缺口)
        # ==================================================
        for i in range(2, len(df) - 1):
            # 向上缺口 (Bullish FVG)：当前 K 线的 low 大于 前两根 K 线的 high
            if df['low'].iloc[i] > df['high'].iloc[i - 2]:
                fvg_top = df['low'].iloc[i]
                fvg_bottom = df['high'].iloc[i - 2]

                # 检查后续 K 线是否跌下来填补了这个缺口 (动态消耗)
                future_lows = df['low'].iloc[i + 1:]
                if not future_lows.empty:
                    lowest_future = future_lows.min()
                    if lowest_future <= fvg_bottom:
                        continue  # 彻底跌穿填满，缺口失效跳过
                    elif lowest_future < fvg_top:
                        fvg_top = lowest_future  # 部分填补，顶端下压！

                if fvg_top > fvg_bottom:
                    pois.append({
                        'type': f'{tf_label}_FVG',
                        'top': fvg_top,
                        'bottom': fvg_bottom,
                        'time': df.index[i]
                    })

        return pois

    def is_in_poi(self, price: float) -> tuple[bool, str]:
        """三号引擎调用接口：传入探底针尖价格，判定命中优先级"""
        if not self.active_pois:
            return False, "无结构"

        # 🌟 优先级 1：钢铁防线！(无视时间级别，1H/15m/5m 只要是实体结构，全算 True！)
        for poi in self.active_pois:
            if 'FVG' not in poi['type']:
                if poi['bottom'] <= price <= poi['top']:
                    return True, f"命中 {poi['type']} 支撑区 ({poi['bottom']:.1f} ~ {poi['top']:.1f})"

        # 🌟 优先级 2：致命悬空！(跌进了 FVG 真空，且没有碰到任何实体)
        for poi in self.active_pois:
            if 'FVG' in poi['type']:
                if poi['bottom'] <= price <= poi['top']:
                    return False, f"⚠️ 致命悬空！探底针尖处于 {poi['type']} 真空区内，未触碰任何实体支撑，极易二次暴跌！"

        return False, "悬空"

    def get_nearest_resistance(self, current_price: float):
        """🌟 进阶版：寻找上方最近的阻力位 (Bearish OB 或 实体 Swing High)"""
        try:
            df = self.loaders["5m"].fetch_historical_data(limit=300).copy()
            if df is None or df.empty:
                return None

            resistances = []

            # 1. 寻找看空订单块 (Colorless Bearish OB: 暴跌前的动能蓄力区)
            for i in range(len(df) - 5, 5, -1):
                # 🌟 无视颜色，计算实体大小
                ob_body = abs(df['open'].iloc[i] - df['close'].iloc[i])
                ob_height = df['high'].iloc[i] - df['low'].iloc[i]

                # 提取下一根 K 线，验证空头动能爆发 (Bearish Displacement)
                next_candle = df.iloc[i + 1]
                next_body = next_candle['open'] - next_candle['close']  # 空头吞没，open大于close

                # 1. 爆发必须是强力阴线 (next_body > 0)
                # 2. 空头吞没验证：爆发阴线的实体必须大于蓄力 K 线的实体
                if next_body <= 0 or next_body <= ob_body:
                    continue

                    # 测量后续 3 根 K 线的向下总推力
                future_drop = df['high'].iloc[i] - df['close'].iloc[i + 1:i + 4].min()
                atr = df['ATR'].iloc[i] if 'ATR' in df.columns else (current_price * 0.002)

                is_strong_drop = future_drop > (1.5 * atr)

                if is_strong_drop:
                    # 🌟 核心：挂在看空订单块的【实体底部】，这叫抢跑流动性 (Front-running)
                    ob_bottom = min(df['open'].iloc[i], df['close'].iloc[i])
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
        logger.info(f"📡 [MTF雷达] 已启动多维静默扫描！将精确对齐 5m 周期换线瞬间抓取全量 K 线...")

        # 解析当前的 timeframe 是多少分钟
        tf_minutes = 5

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
    radar = MicroSMCRadar(symbol="ETH-USDT-SWAP", timeframes=["5m", "15m", "1H"])
    radar.update_structure()
    print("\n🗺️ 当前算出的 5m 支撑防线：")
    for p in radar.active_pois:
        print(f"[{p['type']}] 顶部: {p['top']}, 底部: {p['bottom']}, 生成时间: {p['time']}")

    test_price = 2050.0
    is_safe, msg = radar.is_in_poi(test_price)
    print(f"\n现价 {test_price} 能否抄底？ -> {is_safe} ({msg})")
