#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/2/26 11:26 PM
@File       : strategy.py
@Description: 
"""
import os
import sys
import time
import datetime
import asyncio

# engines/engine_2_smc/strategy.py
import pandas as pd

# 确保能导入 src 目录下的模块
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.data_feed.okx_loader import OKXDataLoader
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

    def update_structure(self):
        """定期拉取最新 K 线，重新绘制 5m SMC 支撑区"""
        try:
            # 拉取最近 100 根 5 分钟 K 线 (大约 8 小时的数据，足够日内剥头皮用了)
            df = self.loader.fetch_historical_data(limit=100)
            # 🌟 增加对 None 的保护，防止网络闪断报错
            if df is None or df.empty or len(df) < 5:
                logger.warning("⚠️ [SMC雷达] 拉取数据为空，跳过本次更新。")
                return

            self.active_pois = self._calculate_support_pois(df)
            logger.debug(f"🗺️ [SMC雷达] 5m结构更新完毕，当前发现 {len(self.active_pois)} 个有效支撑区(POI)。")

        except Exception as e:
            logger.exception(f"❌ [SMC雷达] 更新K线结构失败: {e}")

    def _calculate_support_pois(self, df: pd.DataFrame) -> list:
        """
        寻找 FVG, Swing Low 和 庄家老巢 Order Block，并自动过滤诱导陷阱 (Inducement)
        """
        pois = []
        current_price = df['close'].iloc[-1]
        
        if 'ATR' not in df.columns:
            df['ATR'] = (df['high'] - df['low']).rolling(window=14).mean().fillna(current_price * 0.002)

        current_atr = df['ATR'].iloc[-1]
        
        # 🌟 定义“靠在一起”的距离阈值：1.5倍的当前平均波动率 (大约是现价的 0.2%~0.3%)
        cluster_threshold = 1.5 * current_atr

        def is_inducement(test_bottom, existing_pois):
            """判断是否为诱导陷阱：如果距离某个更低的防线太近，那它就是庄家用来扫损的假防线"""
            for ep in existing_pois:
                distance = abs(test_bottom - ep['bottom'])
                # 如果极其靠近，且测试的防线比已有防线高，则判定为 IDM 陷阱
                if distance < cluster_threshold and test_bottom > ep['bottom']:
                    return True
            return False

        # ==================================================
        # 1. 首先寻找“定海神针”：极值订单块 (Order Block)
        # 把它作为最坚固的底层基石，后续的 FVG 如果离它太近都会被剔除！
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
                        pois.append({
                            'type': 'Order_Block',
                            'top': ob_top,
                            'bottom': ob_bottom,
                            'time': df.index[i]
                        })
                        break  # 极值订单块只取最近且最稳固的 1 个

        # ==================================================
        # 2. 寻找波段低点 (Swing Low) - 自动剔除并向前补充
        # ==================================================
        df['is_swing_low'] = ((df['low'] < df['low'].shift(1)) & (df['low'] < df['low'].shift(2)) &
                              (df['low'] < df['low'].shift(-1)) & (df['low'] < df['low'].shift(-2)))
        swing_lows = df[(df['is_swing_low'] == True) & (df['low'] < current_price)]
        
        sweep_allowance = current_price * 0.0015 
        top_allowance = current_price * 0.0005
        
        valid_sls = 0
        # iloc[::-1] 让数据从右向左（从近到远）扫描
        for idx, row in swing_lows.iloc[::-1].iterrows():
            if valid_sls >= 2: break  # 凑齐 2 个真实波段低点就停止
            
            sl_bottom = row['low'] - sweep_allowance
            if not is_inducement(sl_bottom, pois):
                pois.append({
                    'type': 'Swing_Low', 
                    'top': row['low'] + top_allowance, 
                    'bottom': sl_bottom, 
                    'time': idx
                })
                valid_sls += 1
            else:
                logger.debug(f"🧹 [SMC防线收缩] 发现诱导性 Swing Low，已自动抛弃！")

        # # ==================================================
        # # 3. 🌟 寻找看多缺口 (FVG) - 完美实现你的“向前补充”逻辑
        # # ==================================================
        # df['fvg_gap_bottom'] = df['high'].shift(2)
        # df['fvg_gap_top'] = df['low']
        # bullish_fvgs = df[(df['fvg_gap_bottom'] < df['fvg_gap_top']) & (df['fvg_gap_top'] < current_price)]
        #
        # valid_fvgs = 0
        # for idx, row in bullish_fvgs.iloc[::-1].iterrows():
        #     if valid_fvgs >= 2: break  # 凑齐 2 个纯净 FVG 就停止
        #
        #     fvg_bottom = row['fvg_gap_bottom']
        #     if not is_inducement(fvg_bottom, pois):
        #         pois.append({
        #             'type': 'FVG',
        #             'top': row['fvg_gap_top'],
        #             'bottom': fvg_bottom,
        #             'time': idx
        #         })
        #         valid_fvgs += 1  # 只有真正收录了，计数器才+1
        #     else:
        #         # 🌟 如果被判定为诱导陷阱：不会执行 valid_fvgs += 1
        #         # 循环会自动走向上一根更老的 K 线，完美实现“向前补充一个 FVG”！
        #         logger.debug(f"🧹 [SMC防线收缩] 发现与底部集群重合的诱导 FVG，剔除并向前补充！")

        # ==================================================
        # 4. 🌟 寻找顶底转换区 (Broken Swing High 压力转支撑)
        # ==================================================
        # 识别出所有的 Swing High
        df['is_swing_high'] = ((df['high'] > df['high'].shift(1)) & (df['high'] > df['high'].shift(2)) &
                               (df['high'] > df['high'].shift(-1)) & (df['high'] > df['high'].shift(-2)))

        # 🌟 核心修复：允许现价向下刺穿前高最多 0.3%。
        # 这样在极速砸盘、刚跌破前高针尖的瞬间，这个支撑位依然会死死钉在雷达屏幕上！
        tolerance_buffer = current_price * 0.003
        broken_swing_highs = df[(df['is_swing_high'] == True) & (df['high'] < current_price + tolerance_buffer)]

        valid_bsh = 0
        for idx, row in broken_swing_highs.iloc[::-1].iterrows():
            if valid_bsh >= 2: break  # 找到最近的 2 个顶底转换位即可

            # 以这个前高的最高点为基准，画一个支撑带
            bsh_top = row['high'] + top_allowance
            bsh_bottom = row['high'] - sweep_allowance

            # 同样利用我们的防诱导逻辑，如果离得太近就合并/剔除
            if not is_inducement(bsh_bottom, pois):
                pois.append({
                    'type': 'Broken_Swing_High',
                    'top': bsh_top,
                    'bottom': bsh_bottom,
                    'time': idx
                })
                valid_bsh += 1
            else:
                logger.debug(f"🧹 [SMC防线收缩] 发现重合的 Broken Swing High 支撑，剔除并向前补充！")

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
            df = self.loader.fetch_historical_data(limit=50)
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
