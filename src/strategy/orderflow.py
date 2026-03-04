# src/strategy/orderflow.py
from collections import deque

class OrderFlowStrategy:
    def __init__(self):
        self.cvd = 0.0
        self.snapshots = deque(maxlen=300)
        # ...

    def process_new_tick(self, tick_data) -> dict:
        """
        接收最新的 tick，更新 CVD，计算背离。
        如果发现背离，返回 signal 字典；否则返回 None。
        """
        # ... 在这里计算 CVD 和流速 ...
        if price_is_near_low and massive_selling_absorbed and time_passed:
             return {
                 "signal_type": "流速级抄底绝杀",
                 "price": current_snap['price'],
                 "cvd_delta_usdt": recent_cvd_delta_usdt
             }
        return None
