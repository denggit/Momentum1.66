import requests
import pandas as pd
import time
import logging

from config.loader import TIMEZONE

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class OKXDataLoader:
    def __init__(self, symbol: str, timeframe: str):
        """
        初始化原生 OKX 数据加载器
        :param symbol: 交易对，如 'ETH-USDT-SWAP'
        :param timeframe: 周期，如 '15m'
        """
        self.base_url = "https://www.okx.com"
        self.symbol = symbol
        self.timeframe = timeframe

    def fetch_historical_data(self, limit: int = 500, retries: int = 3) -> pd.DataFrame:
        """
        原生调用 OKX V5 接口拉取 K 线
        """
        endpoint = "/api/v5/market/history-candles"
        url = f"{self.base_url}{endpoint}"

        all_candles = []
        after = ""  # 用于分页的请求游标

        logging.info(f"开始通过原生 API 拉取 {self.symbol} {self.timeframe} 数据，目标 {limit} 根...")

        while len(all_candles) < limit:
            # OKX 每次最大支持 300 根
            fetch_size = min(300, limit - len(all_candles))
            params = {
                "instId": self.symbol,
                "bar": self.timeframe,
                "limit": fetch_size
            }
            if after:
                params["after"] = after

            candles = []
            for attempt in range(retries):
                try:
                    response = requests.get(url, params=params, timeout=10)
                    response.raise_for_status()
                    data = response.json()

                    if data["code"] != "0":
                        raise ValueError(f"OKX 业务报错: {data['msg']}")

                    candles = data["data"]
                    if not candles:
                        break  # 已经没有更多数据了

                    all_candles.extend(candles)
                    # 取最后一根K线的时间戳，作为下一次请求的游标
                    after = candles[-1][0]
                    break  # 成功获取本页数据，跳出重试循环

                except Exception as e:
                    logging.error(f"第 {attempt + 1} 次请求失败: {e}")
                    if attempt == retries - 1:
                        raise ConnectionError(f"API 请求彻底失败: {e}")
                    time.sleep(1)  # 失败后等 1 秒再试

            if not candles:
                break
            time.sleep(0.1)  # 频率保护：每秒最多 20 次请求

        if not all_candles:
            logging.warning("未拉取到任何数据！")
            return pd.DataFrame()

        # OKX 原始数据格式: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        df = pd.DataFrame(all_candles,
                          columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'volCcy', 'volCcyQuote',
                                   'confirm'])

        # 只保留量化需要的核心 6 列
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        # 将字符串转为浮点数
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        # 转换时间戳 (并加 8 小时转换为东八区时间)
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        if "+" in TIMEZONE:
            df['timestamp'] += pd.Timedelta(hours=int(TIMEZONE.split("+")[-1]))
        elif "-" in TIMEZONE:
            df['timestamp'] += pd.Timedelta(hours=int(TIMEZONE.split("-")[-1]))

        # **非常重要**：OKX 接口返回的数据是最新的在最前面 (倒序)
        # 必须反转排序，变成最旧的在前面，否则以后所有的 EMA 和布林带计算全都会算错！
        df.sort_values('timestamp', ascending=True, inplace=True)
        df.set_index('timestamp', inplace=True)

        logging.info(f"成功构建 DataFrame，共 {len(df)} 根 K 线。最新时间: {df.index[-1]}")
        return df