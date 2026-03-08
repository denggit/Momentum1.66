import numpy as np
import pandas as pd
import xgboost as xgb  # 引入 XGBoost

from src.utils.log import get_logger

logger = get_logger(__name__)


class SMCStrategy:
    def __init__(self, ema_period=144, lookback=15, atr_mult=1.5, ob_expiry=72, sl_buffer=0.6, entry_buffer=-0.1,
                 ai_config=None):
        self.ema_period = ema_period
        self.lookback = lookback
        self.atr_mult = atr_mult
        self.ob_expiry = ob_expiry
        self.sl_buffer = sl_buffer
        self.entry_buffer = entry_buffer

        # ==========================================
        # 🤖 初始化 AI 风控模块
        # ==========================================
        self.ai_config = ai_config or {}
        self.ai_enabled = self.ai_config.get('enabled', False)
        self.ai_threshold = self.ai_config.get('threshold', 0.35)  # 默认 35% 放行
        self.ai_model = None

        if self.ai_enabled:
            model_path = self.ai_config.get('model_path')
            try:
                self.ai_model = xgb.XGBClassifier()
                self.ai_model.load_model(model_path)
                logger.info(f"🤖 [系统就绪] AI 风控模型加载成功! (拦截阈值: {self.ai_threshold})")
            except Exception as e:
                logger.info(f"⚠️ [警告] AI 模型加载失败，已自动降级为传统规则模式。错误: {e}")
                self.ai_enabled = False

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['Signal'] = 0
        df['SL_Price'] = np.nan

        # 提取基础数组
        open_p, high, low, close = df['open'].values, df['high'].values, df['low'].values, df['close'].values
        atr, atr_rank = df['ATR'].values, df['ATR_Rank'].values
        highest_high = df['high'].rolling(self.lookback).max().shift(1).values
        lowest_low = df['low'].rolling(self.lookback).min().shift(1).values

        # 提取 AI 特征数组
        hour_arr = df['Hour'].values
        dow_arr = df['DayOfWeek'].values
        dist_ema_arr = df['Dist_to_EMA'].values
        adx_arr = df['ADX'].values
        rsi_arr = df['RSI'].values
        atr_slope_arr = df['ATR_Slope'].values
        body_ratio_arr = df['Body_Ratio'].values

        signals = np.zeros(len(df))
        sl_prices = np.full(len(df), np.nan)

        long_ob_top, long_ob_bot, long_ob_active, long_ob_age = 0.0, 0.0, False, 0
        short_ob_top, short_ob_bot, short_ob_active, short_ob_age = float('inf'), float('inf'), False, 0

        for i in range(self.lookback, len(df)):
            if atr_rank[i] <= 0.7:
                continue

            if long_ob_active: long_ob_age += 1
            if short_ob_active: short_ob_age += 1
            if long_ob_age > self.ob_expiry: long_ob_active = False
            if short_ob_age > self.ob_expiry: short_ob_active = False

            # ==========================================
            # 1. 猎杀时刻 (进场触发与 AI 终审)
            # ==========================================
            if long_ob_active:
                long_entry_trigger = long_ob_top + (atr[i] * self.entry_buffer)
                if low[i] <= long_entry_trigger and close[i] > long_ob_bot:
                    # 准备开多单，计算止损和厚度
                    potential_sl = long_ob_bot - (atr[i] * self.sl_buffer)
                    sl_pct = ((close[i] - potential_sl) / close[i]) * 100

                    # 🤖 AI 拦截审核
                    if self.ai_enabled and self.ai_model is not None:
                        # 顺序必须与训练时完全一致: 'Hour', 'DayOfWeek', 'Dist_to_EMA', 'ADX', 'RSI', 'ATR_Rank', 'ATR_Slope', 'Body_Ratio', 'sl_pct'
                        features = np.array([[
                            hour_arr[i], dow_arr[i], dist_ema_arr[i], adx_arr[i], rsi_arr[i],
                            atr_rank[i], atr_slope_arr[i], body_ratio_arr[i], sl_pct
                        ]])

                        prob_win = self.ai_model.predict_proba(features)[0][1]
                        if prob_win < self.ai_threshold:
                            long_ob_active = False  # 被判定为死局，抹除订单块，跳过开仓
                            continue

                            # 审核通过，扣动扳机！
                    signals[i] = 1
                    sl_prices[i] = potential_sl
                    long_ob_active = False
                elif close[i] < long_ob_bot:
                    long_ob_active = False

            if short_ob_active:
                short_entry_trigger = short_ob_bot - (atr[i] * self.entry_buffer)
                if high[i] >= short_entry_trigger and close[i] < short_ob_top:
                    # 准备开空单，计算止损和厚度
                    potential_sl = short_ob_top + (atr[i] * self.sl_buffer)
                    sl_pct = ((potential_sl - close[i]) / close[i]) * 100

                    # 🤖 AI 拦截审核
                    if self.ai_enabled and self.ai_model is not None:
                        features = np.array([[
                            hour_arr[i], dow_arr[i], dist_ema_arr[i], adx_arr[i], rsi_arr[i],
                            atr_rank[i], atr_slope_arr[i], body_ratio_arr[i], sl_pct
                        ]])

                        prob_win = self.ai_model.predict_proba(features)[0][1]
                        if prob_win < self.ai_threshold:
                            short_ob_active = False  # 被判定为死局，抹除订单块，跳过开仓
                            continue

                            # 审核通过，扣动扳机！
                    signals[i] = -1
                    sl_prices[i] = potential_sl
                    short_ob_active = False
                elif close[i] > short_ob_top:
                    short_ob_active = False

            # ==========================================
            # 2. 寻找动能建仓结构 (原汁原味的 1H 画线逻辑)
            # ==========================================
            # 多头
            if close[i] > open_p[i] and (close[i] - open_p[i]) > self.atr_mult * atr[i]:
                if close[i] > highest_high[i]:
                    for j in range(i - 1, max(-1, i - 10), -1):
                        if close[j] < open_p[j]:
                            long_ob_top, long_ob_bot, long_ob_active, long_ob_age = high[j], low[j], True, 0
                            break
            # 空头
            elif close[i] < open_p[i] and (open_p[i] - close[i]) > self.atr_mult * atr[i]:
                if close[i] < lowest_low[i]:
                    for j in range(i - 1, max(-1, i - 10), -1):
                        if close[j] > open_p[j]:
                            short_ob_top, short_ob_bot, short_ob_active, short_ob_age = high[j], low[j], True, 0
                            break

        df['Signal'] = signals
        df['SL_Price'] = sl_prices
        return df
