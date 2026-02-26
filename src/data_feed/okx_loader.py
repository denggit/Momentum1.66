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
        """
        self.base_url = "https://www.okx.com"
        self.symbol = symbol
        self.timeframe = timeframe
        # 【新增】使用 Session 维持连接池，提高效率并在断开时可以重置
        self.session = requests.Session()

    def fetch_historical_data(self, limit: int = 5000, max_retries: int = 10) -> pd.DataFrame:
        """
        原生调用 OKX V5 接口拉取历史 K 线 (自动分批防封版)
        """
        endpoint = "/api/v5/market/history-candles"
        url = f"{self.base_url}{endpoint}"

        all_candles = []
        after = ""

        logging.info(f"开始通过原生 API 批量拉取 {self.symbol} {self.timeframe} 数据，目标 {limit} 根...")

        # 核心参数：每拉取多少根进行一次深度休眠断点
        batch_size_threshold = 1000

        while len(all_candles) < limit:
            # OKX 每次最大支持 100 根
            fetch_size = min(100, limit - len(all_candles))
            params = {
                "instId": self.symbol,
                "bar": self.timeframe,
                "limit": fetch_size
            }
            if after:
                params["after"] = after

            candles = []
            success = False

            for attempt in range(max_retries):
                try:
                    # 使用 session 发起请求
                    response = self.session.get(url, params=params, timeout=15)
                    response.raise_for_status()
                    data = response.json()

                    if data["code"] != "0":
                        raise ValueError(f"OKX 业务报错: {data['msg']}")

                    candles = data["data"]
                    if not candles:
                        success = True
                        break

                    all_candles.extend(candles)
                    after = candles[-1][0]
                    success = True

                    # 打印精细进度
                    if len(all_candles) % 500 == 0 or len(all_candles) == limit:
                        logging.info(f"拉取进度: {len(all_candles)} / {limit} ...")

                    break  # 成功，跳出重试循环

                except Exception as e:
                    # 【核心机制 1】遭遇代理断开或超时，销毁并重建底层 TCP 连接！
                    logging.warning(
                        f"网络颠簸 (进度 {len(all_candles)}/{limit}) | 第 {attempt + 1}/{max_retries} 次重试... 报错: {e}")
                    self.session.close()
                    self.session = requests.Session()

                    # 【核心机制 2】指数退避休眠：3秒, 5秒, 7秒... 越失败休息越久
                    sleep_time = 3 + (attempt * 2)
                    time.sleep(sleep_time)

            if not success or not candles:
                logging.error(f"严重网络故障或无更多数据。停止拉取！将返回已成功获取的 {len(all_candles)} 根数据。")
                break

            # 【核心机制 3】大批次深度休眠防封锁
            if len(all_candles) > 0 and len(all_candles) % batch_size_threshold == 0:
                logging.info(f"🟢 已完成一个大批次 ({len(all_candles)}根)，强制休眠 3 秒，释放代理与服务器连接压力...")
                time.sleep(3)
            else:
                time.sleep(0.15)  # 平时的正常频率保护

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

        # 转换时间戳
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        if "+" in TIMEZONE:
            df['timestamp'] += pd.Timedelta(hours=int(TIMEZONE.split("+")[-1]))
        elif "-" in TIMEZONE:
            df['timestamp'] += pd.Timedelta(hours=int(TIMEZONE.split("-")[-1]))

        # 反转排序，最旧的在前面
        df.sort_values('timestamp', ascending=True, inplace=True)
        df.set_index('timestamp', inplace=True)

        logging.info(f"✅ 成功构建 DataFrame，共 {len(df)} 根 K 线。最旧时间: {df.index[0]} | 最新时间: {df.index[-1]}")
        return df