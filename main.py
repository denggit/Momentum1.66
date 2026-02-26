from config.loader import SYMBOL, TIMEFRAME
from src.data_feed.okx_loader import OKXDataLoader


if __name__ == "__main__":
    # 直接实例化，不用管什么 ccxt 和代理了
    loader = OKXDataLoader(symbol=SYMBOL, timeframe=TIMEFRAME)
    df = loader.fetch_historical_data(limit=500)

    print("\n--- ETH 永续合约 最近 5 根 K 线数据 ---")
    print(df.tail())