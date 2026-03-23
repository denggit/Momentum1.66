#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import os
import sys

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(current_file))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backtest.engine import run_universal_backtest
from config.loader import load_strategy_config
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_smc_indicators
from src.strategy.smc import SMCStrategy
from src.utils.log import get_logger

logger = get_logger(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

START_DATE = '2020-01-01'
END_DATE = '2025-12-31'
SMC_TIMEFRAME = '1H'  # 波段交易，回归 1H 大气层！
SYMBOL = 'ETH-USDT-SWAP'
cfg = load_strategy_config("smc", SYMBOL)
strat_cfg = cfg.get('strategy', {})
engine_cfg = cfg.get("engine", {})
ai_cfg = cfg.get("ai_filter", {})
ai_enabled = False

if __name__ == "__main__":
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=SMC_TIMEFRAME)
    df = loader.fetch_data_by_date_range(START_DATE, END_DATE)
    logger.info(f"获取数据完成，形状: {df.shape if not df.empty else '空'}，列: {list(df.columns) if not df.empty else []}")

    if df.empty:
        logger.info(f"数据为空！")
    else:
        # 1. 挂载 SMC 需要的均线和 ATR
        df = add_smc_indicators(df)
        logger.info(f"添加SMC指标后，形状: {df.shape}")

        # 2. 生成聪明的订单块回踩信号
        strategy = SMCStrategy(ema_period=strat_cfg.get('ema_period'),
                               lookback=strat_cfg.get('lookback'),
                               atr_mult=strat_cfg.get('atr_mult'),
                               ob_expiry=strat_cfg.get('ob_expiry'),
                               sl_buffer=strat_cfg.get('sl_buffer'),
                               entry_buffer=strat_cfg.get('entry_buffer'),
                               ai_config={
                                   'enabled': ai_enabled,
                                   'model_path': ai_cfg.get("model_path"),
                                   'threshold': ai_cfg.get("threshold")  # 只要 AI 觉得这单有 35% 以上可能不是杀猪盘，就干！
                               }
                               )
        df = strategy.generate_signals(df)

        # 3. 呼叫全能引擎！
        # 进场：SMC 左侧挂单回踩
        # 出场：极其广阔的 4.5 倍 ATR 吊灯追踪，一口吃穿整个趋势！
        run_universal_backtest(
            df=df,
            strategy_name=f"SMC ({SMC_TIMEFRAME} Order Block) {SYMBOL}",
            symbol=SYMBOL,
            initial_capital=engine_cfg.get("initial_capital"),
            max_risk=engine_cfg.get("max_risk"),
            atr_multiplier=engine_cfg.get("atr_multiplier"),  # 沿用一号引擎神级参数，死死咬住波段
            fee_rate=engine_cfg.get("fee_rate"),  # Taker 手续费 (因为我们是在K线收盘确认触发)
            time_stop=engine_cfg.get("time_stop"),
            ai_enabled=ai_enabled
        )
