import logging
import os
import sqlite3
import time

import pandas as pd
import requests

# 确保引入你的时区配置
try:
    from config.loader import TIMEZONE
except ImportError:
    TIMEZONE = "+8"  # 兜底默认值

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class OKXDataLoader:
    def __init__(self, symbol="ETH-USDT-SWAP", timeframe="1H", db_dir=None):
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_url = "https://www.okx.com"

        self.session = requests.Session()

        self.bar_map = {
            '1m': '1m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1H': '1H',
            '4H': '4H',
            '1D': '1D'
        }
        if timeframe not in self.bar_map:
            raise IndexError(f"没有这个timeframe: {timeframe}")
        self.okx_bar = self.bar_map.get(timeframe)

        # ==========================================
        # 核心修改：利用 __file__ 动态获取项目根目录
        # ==========================================
        if db_dir is None:
            # 获取 okx_loader.py 的绝对路径
            current_file = os.path.abspath(__file__)
            # 向上推三层：okx_loader.py -> data_feed -> src -> 根目录 (Momentum1.66)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
            # 强行把数据库目录锁定在项目根目录下的 data 文件夹里
            db_dir = os.path.join(project_root, 'data')

        if not os.path.exists(db_dir):
            os.makedirs(db_dir)

        self.db_path = os.path.join(db_dir, 'crypto_history.db')
        self.table_name = f"{symbol.replace('-', '_')}_{timeframe}"

    def _get_db_connection(self):
        return sqlite3.connect(self.db_path)

    def _get_current_local_time(self):
        """获取带有配置时区偏移的当前时间"""
        now_utc = pd.Timestamp.utcnow().tz_localize(None)
        if "+" in TIMEZONE:
            now_utc += pd.Timedelta(hours=int(TIMEZONE.split("+")[-1]))
        elif "-" in TIMEZONE:
            now_utc -= pd.Timedelta(hours=int(TIMEZONE.split("-")[-1]))
        return now_utc

    def load_local_data(self) -> pd.DataFrame:
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT count(name) FROM sqlite_master WHERE type='table' AND name='{self.table_name}'")
            if cursor.fetchone()[0] == 0:
                conn.close()
                return pd.DataFrame()

            df = pd.read_sql(f"SELECT * FROM {self.table_name}", conn, index_col='timestamp', parse_dates=['timestamp'])
            conn.close()
            return df
        except Exception as e:
            logging.error(f"读取本地数据库失败: {e}")
            return pd.DataFrame()

    def save_local_data(self, df: pd.DataFrame):
        if df.empty:
            return
        conn = self._get_db_connection()
        df.to_sql(self.table_name, conn, if_exists='replace', index=True)
        conn.close()
        logging.info(f"💾 成功将 {len(df)} 根 K 线保存至本地数据库: [{self.table_name}]")

    def fetch_from_okx(self, limit=100, after_ts=None, max_retries=10) -> pd.DataFrame:
        """原生调用 OKX V5 接口拉取历史 K 线 (自动分批防封版)"""
        endpoint = "/api/v5/market/history-candles"
        url = f"{self.base_url}{endpoint}"

        all_candles = []
        current_after = after_ts
        batch_size_threshold = 1000

        # logging.info(f"开始通过原生 API 批量拉取 {self.symbol} {self.timeframe} 数据，目标 {limit} 根...")

        while len(all_candles) < limit:
            fetch_size = min(100, limit - len(all_candles))
            params = {
                "instId": self.symbol,
                "bar": self.okx_bar,
                "limit": fetch_size
            }
            if current_after:
                params["after"] = current_after

            candles = []
            success = False

            for attempt in range(max_retries):
                try:
                    response = self.session.get(url, params=params, timeout=15)
                    response.raise_for_status()
                    data = response.json()

                    if data.get("code") != "0":
                        raise ValueError(f"OKX 业务报错: {data.get('msg')}")

                    candles = data.get("data", [])
                    if not candles:
                        success = True
                        break

                    all_candles.extend(candles)
                    current_after = candles[-1][0]
                    success = True

                    if len(all_candles) % 10000 == 0 or len(all_candles) == limit:
                        logging.info(f"拉取进度: {len(all_candles)} / {limit} ...")

                    break

                except Exception as e:
                    logging.warning(
                        f"网络颠簸 (进度 {len(all_candles)}/{limit}) | 第 {attempt + 1}/{max_retries} 次重试... 报错: {e}")
                    self.session.close()
                    self.session = requests.Session()
                    sleep_time = 3 + (attempt * 2)
                    time.sleep(sleep_time)

            if not success or not candles:
                logging.error(f"严重网络故障或无更多数据。停止拉取！将返回已成功获取的 {len(all_candles)} 根数据。")
                break

            if len(all_candles) > 0 and len(all_candles) % batch_size_threshold == 0:
                # logging.debug(f"🟢 已完成一个大批次 ({len(all_candles)}根)，强制休眠 3 秒，防封锁...")
                time.sleep(3)
            else:
                time.sleep(0.15)

        if not all_candles:
            return pd.DataFrame()

        df = pd.DataFrame(all_candles,
                          columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'volCcy', 'volCcyQuote',
                                   'confirm'])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        if "+" in TIMEZONE:
            df['timestamp'] += pd.Timedelta(hours=int(TIMEZONE.split("+")[-1]))
        elif "-" in TIMEZONE:
            df['timestamp'] += pd.Timedelta(hours=int(TIMEZONE.split("-")[-1]))

        df.sort_values('timestamp', ascending=True, inplace=True)
        df.set_index('timestamp', inplace=True)

        current_time = self._get_current_local_time()
        if not df.empty and (current_time - df.index[-1]).total_seconds() < self._get_seconds(self.timeframe):
            df = df.iloc[:-1]

        return df

    def fetch_historical_data(self, limit=50000) -> pd.DataFrame:
        """
        全量智能拼接系统：
        分离了【增量拉取最新数据】和【追溯拉取历史数据】两个动作
        """
        logging.info(f"🔍 准备加载 {self.symbol} ({self.timeframe}) 数据...")
        local_df = self.load_local_data()

        if local_df.empty:
            logging.info(f"⚠️ 本地无数据，将从 OKX 全量拉取 {limit} 根...")
            final_df = self.fetch_from_okx(limit=limit)
            self.save_local_data(final_df)
            return final_df.tail(limit)

        local_count = len(local_df)
        last_local_time = local_df.index[-1]
        oldest_local_time = local_df.index[0]
        logging.info(f"📦 本地数据库已命中！现有 {local_count} 根 K 线 | 区间: {oldest_local_time} -> {last_local_time}")

        current_local = self._get_current_local_time()
        bar_seconds = self._get_seconds(self.timeframe)

        # =======================================
        # 步骤 1: 向右看！补齐【最新】缺失的 K 线
        # =======================================
        time_diff_seconds = (current_local - last_local_time).total_seconds()
        missing_new_bars = int(time_diff_seconds / bar_seconds)

        new_df = pd.DataFrame()
        if missing_new_bars > 0:
            logging.info(f"🔄 准备增量补齐约 {missing_new_bars} 根 最新 K 线...")
            new_df = self.fetch_from_okx(limit=missing_new_bars + 10)

        if not new_df.empty:
            combined_df = pd.concat([local_df, new_df])
            combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
            combined_df = combined_df.sort_index(ascending=True)
        else:
            combined_df = local_df

        # =======================================
        # 步骤 2: 向左看！补齐【更老】的历史 K 线
        # =======================================
        current_count = len(combined_df)
        old_df = pd.DataFrame()

        if current_count < limit:
            missing_old_bars = limit - current_count
            logging.info(f"🔄 本地数据总量不足，准备向前追溯补齐 {missing_old_bars} 根 历史 K 线...")

            # 计算当前库中最老一根 K 线的时间，并逆向剥离时区还原为 UTC 毫秒时间戳
            oldest_local = combined_df.index[0]
            oldest_utc = oldest_local
            if "+" in TIMEZONE:
                oldest_utc -= pd.Timedelta(hours=int(TIMEZONE.split("+")[-1]))
            elif "-" in TIMEZONE:
                oldest_utc += pd.Timedelta(hours=int(TIMEZONE.split("-")[-1]))

            oldest_ts_ms = str(int(oldest_utc.tz_localize('UTC').timestamp() * 1000))

            # 携带 after_ts 拉取更早的数据
            old_df = self.fetch_from_okx(limit=missing_old_bars + 10, after_ts=oldest_ts_ms)

        if not old_df.empty:
            combined_df = pd.concat([old_df, combined_df])
            combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
            combined_df = combined_df.sort_index(ascending=True)

        # =======================================
        # 步骤 3: 保存至本地数据库并返回
        # =======================================
        if not new_df.empty or not old_df.empty:
            self.save_local_data(combined_df)

        return combined_df.tail(limit)

    def _get_seconds(self, timeframe: str) -> int:
        mapping = {
            '1m': 60,
            '5m': 300,
            '15m': 900,
            '30m': 1800,  # <--- 加上 1800 秒！
            '1H': 3600,
            '4H': 14400,
            '1D': 86400
        }
        if timeframe not in mapping:
            raise IndexError(f"没有这个timeframe: {timeframe}")

        return mapping.get(timeframe)
