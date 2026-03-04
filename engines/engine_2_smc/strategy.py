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
            if df.empty or len(df) < 5:
                return

            self.active_pois = self._calculate_support_pois(df)
            logger.debug(f"🗺️ [SMC雷达] 5m结构更新完毕，当前发现 {len(self.active_pois)} 个有效支撑区(POI)。")

        except Exception as e:
            logger.error(f"❌ [SMC雷达] 更新K线结构失败: {e}")

    def _calculate_support_pois(self, df: pd.DataFrame) -> list:
        """
        核心算法升级：找出未回补 FVG、波段低点 SSL 以及 庄家老巢 Order Block
        """
        pois = []
        current_price = df['close'].iloc[-1]
        atr_now = df['ATR'].iloc[-1] if 'ATR' in df.columns else 5.0

        # ==================================================
        # 1. 寻找未回补的看多缺口 (Bullish FVG)
        # ==================================================
        df['fvg_gap_bottom'] = df['high'].shift(2)
        df['fvg_gap_top'] = df['low']
        bullish_fvgs = df[(df['fvg_gap_bottom'] < df['fvg_gap_top']) & (df['fvg_gap_top'] < current_price)]
        for idx, row in bullish_fvgs.tail(2).iterrows():
            pois.append({'type': 'FVG', 'top': row['fvg_gap_top'], 'bottom': row['fvg_gap_bottom'], 'time': idx})

        # ==================================================
        # 2. 寻找波段低点流动性池 (Swing Low / SSL)
        # ==================================================
        df['is_swing_low'] = ((df['low'] < df['low'].shift(1)) & (df['low'] < df['low'].shift(2)) &
                              (df['low'] < df['low'].shift(-1)) & (df['low'] < df['low'].shift(-2)))
        swing_lows = df[(df['is_swing_low'] == True) & (df['low'] < current_price)]
        SWEEP_ALLOWANCE = 3.0
        for idx, row in swing_lows.tail(2).iterrows():
            pois.append(
                {'type': 'Swing_Low', 'top': row['low'] + 1.0, 'bottom': row['low'] - SWEEP_ALLOWANCE, 'time': idx})

        # ==================================================
        # 3. 🌟 核心升级：寻找看多订单块 (Bullish Order Block)
        # ==================================================
        # 逻辑：寻找一段强力上涨（BOS）之前的最后一根阴线
        for i in range(len(df) - 5, 5, -1):
            # 找到一根阴线
            if df['close'].iloc[i] < df['open'].iloc[i]:
                # 检查之后是否有强力拉升（3根K线内涨幅超过 1.5倍 ATR）
                future_move = df['close'].iloc[i + 1:i + 4].max() - df['close'].iloc[i]
                if future_move > (1.5 * df['ATR'].iloc[i]):
                    ob_top = df['high'].iloc[i]
                    ob_bottom = df['low'].iloc[i]
                    # 如果 OB 还在当前价格下方，则视为有效
                    if ob_top < current_price:
                        pois.append({
                            'type': 'Order_Block',
                            'top': ob_top,
                            'bottom': ob_bottom,
                            'time': df.index[i]
                        })
                        break  # 只抓最近的一个核心 OB 即可

        return pois

    def is_in_poi(self, price: float) -> tuple[bool, str]:
        """
        三号引擎调用接口：传入当前价格，问雷达兵“这里能开火吗？”
        返回 (True/False, 区域描述)
        """
        self.update_structure()  # 每次询问时，顺便检查需不需要更新数据

        if not self.active_pois:
            return False, "无结构"

        for poi in self.active_pois:
            if poi['bottom'] <= price <= poi['top']:
                return True, f"命中 {poi['type']} 支撑区 ({poi['bottom']:.1f} ~ {poi['top']:.1f})"

        return False, "悬空"

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

        while True:
            import datetime
            import asyncio
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
