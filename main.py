from config.loader import SYMBOL, TIMEFRAME, SQZ_PARAMS
from src.data_feed.okx_loader import OKXDataLoader
from src.strategy.indicators import add_squeeze_indicators
from src.strategy.squeeze import SqueezeStrategy

if __name__ == "__main__":
    # 1. 加载配置
    symbol = SYMBOL
    timeframe = TIMEFRAME

    # 策略参数
    sqz_params = SQZ_PARAMS

    # 2. 拉取数据 (这里我们多拉一点，拉 1000 根，方便看历史信号)
    loader = OKXDataLoader(symbol=symbol, timeframe=timeframe)
    df = loader.fetch_historical_data(limit=5000)

    if not df.empty:
        # 3. 计算技术指标
        df = add_squeeze_indicators(
            df=df,
            bb_len=sqz_params['bb_length'],
            bb_std=sqz_params['bb_std'],
            kc_len=sqz_params['kc_length'],
            kc_mult=sqz_params['kc_mult']
        )

        # 4. 生成交易信号
        strategy = SqueezeStrategy(volume_factor=sqz_params['volume_factor'])
        df = strategy.generate_signals(df)

        # 5. 打印出所有出现开仓信号的时间点！
        signals_df = df[df['Signal'] != 0].copy()

        print("\n=== 历史触发的 Squeeze 突破信号清单 ===")
        if not signals_df.empty:
            for index, row in signals_df.iterrows():
                direction = "🟢 做多 (LONG)" if row['Signal'] == 1 else "🔴 做空 (SHORT)"
                print(
                    f"时间: {index} | 方向: {direction} | 突破价格: {row['close']} | 放量确认: {row['volume']:.2f} > 均量 {row['Vol_SMA']:.2f}")
        else:
            print("这段时间内没有触发完美的挤压突破信号 (市场太震荡或者没有放量)。")
