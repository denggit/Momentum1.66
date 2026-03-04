# src/data_feed/okx_stream.py
class OKXTickStreamer:
    def __init__(self, symbol="ETH-USDT-SWAP", on_tick_callback=None):
        self.symbol = symbol
        self.on_tick_callback = on_tick_callback # 核心：通过回调抛出数据
        self.ws_url = "wss://wsaws.okx.com:8443/ws/v5/public"

    async def connect(self):
        # ... 只保留 websocket 连接和 JSON 解析代码 ...
        if 'data' in data and self.on_tick_callback:
            for trade in data['data']:
                # 把清洗后的标准字典传给外部
                self.on_tick_callback({
                    'price': float(trade['px']),
                    'size': float(trade['sz']),
                    'side': trade['side'],
                    'ts': float(trade['ts']) / 1000.0
                })
