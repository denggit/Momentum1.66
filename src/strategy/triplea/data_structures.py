"""
四号引擎v3.0 核心数据结构定义
使用Python dataclass定义所有核心数据结构和配置类
保持与现有API 100%兼容，支持高性能序列化
"""

from dataclasses import dataclass, field
from typing import Dict, Any


# 原始输入数据结构
@dataclass
class OKXRawTick:
    """原始Tick输入（来自OKX WebSocket）"""
    instId: str  # 交易对标识符，如 "ETH-USDT-SWAP"
    tradeId: str  # 撮合ID（交易所唯一标识）
    ts: int  # 纳秒级时间戳 (unix epoch nanoseconds)
    px: float  # 成交价格
    sz: float  # 成交数量 (币数)
    side: str  # 主动方向 "buy" 或 "sell" (主动发起方)


@dataclass
class NormalizedTick:
    """内部标准化Tick（进入处理流水线）"""
    ts: int  # 纳秒时间戳
    px: float  # 价格
    sz: float  # 数量
    side: int  # +1 (buy主动吃单), -1 (sell主动吃单)

    @classmethod
    def from_raw_tick(cls, raw_tick: OKXRawTick) -> 'NormalizedTick':
        """从原始Tick创建标准化Tick"""
        side_int = 1 if raw_tick.side.lower() == 'buy' else -1
        return cls(
            ts=raw_tick.ts,
            px=raw_tick.px,
            sz=raw_tick.sz,
            side=side_int
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化"""
        return {
            'ts': self.ts,
            'px': self.px,
            'sz': self.sz,
            'side': self.side
        }


@dataclass
class RangeBar:
    """Range Bar（无时间维度容器）"""
    open_ts: int  # 首笔Tick时间戳
    open_px: float  # 开盘价
    high_px: float  # 最高价
    low_px: float  # 最低价
    close_px: float  # 收盘价（触发下根Bar的价格）
    total_buy_vol: float  # 区间内主动买量 (side=+1)
    total_sell_vol: float  # 区间内主动卖量 (side=-1)
    delta: float  # total_buy_vol - total_sell_vol
    tick_count: int  # 包含的原始Tick数

    def __post_init__(self):
        """后初始化处理，确保数据一致性"""
        # 确保high_px >= low_px
        if self.high_px < self.low_px:
            self.high_px, self.low_px = self.low_px, self.high_px

        # 计算delta
        self.delta = self.total_buy_vol - self.total_sell_vol

    def get_volume(self) -> float:
        """获取总成交量"""
        return self.total_buy_vol + self.total_sell_vol

    def is_bullish(self) -> bool:
        """判断是否为上涨Bar (收盘价 > 开盘价)"""
        return self.close_px > self.open_px

    def is_bearish(self) -> bool:
        """判断是否为下跌Bar (收盘价 < 开盘价)"""
        return self.close_px < self.open_px

    def is_neutral(self) -> bool:
        """判断是否为中性Bar (收盘价 = 开盘价)"""
        return self.close_px == self.open_px

    def get_price_range_ticks(self, tick_size: float = 0.01) -> float:
        """获取价格范围（Tick单位）"""
        price_range = self.high_px - self.low_px
        return price_range / tick_size

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化"""
        return {
            'open_ts': self.open_ts,
            'open_px': self.open_px,
            'high_px': self.high_px,
            'low_px': self.low_px,
            'close_px': self.close_px,
            'total_buy_vol': self.total_buy_vol,
            'total_sell_vol': self.total_sell_vol,
            'delta': self.delta,
            'tick_count': self.tick_count
        }


# 配置数据结构
@dataclass
class MarketConfig:
    """市场配置（合约规格）"""
    instId: str = "ETH-USDT-SWAP"  # 交易对标识符
    tick_size: float = 0.01  # ETH永续合约最小变动价位
    price_precision: int = 2  # 价格精度小数位


@dataclass
class DataPipelineConfig:
    """数据流水线配置"""
    ring_buffer_size: int = 1048576  # 2^20 个Tick槽位（使用deque预分配）
    ws_reconnect_interval_ms: int = 5000


@dataclass
class RangeBarConfig:
    """Range Bar配置"""
    tick_range: int = 20  # 20个Tick构成一根Range Bar
    tick_size: float = 0.01  # 最小价格变动单位（ETH永续合约）
    max_bar_history: int = 1440  # 保留最近1440根Bar


@dataclass
class KDEEngineConfig:
    """KDE引擎配置"""
    bandwidth_method: str = "silverman_robust"  # 稳健Silverman法则
    lvn_density_percentile: float = 30.0  # 密度低于30%分位认定为LVN
    min_slice_ticks: int = 100  # 脉冲波最少包含100笔Tick才计算KDE

    # 自适应网格配置（实盘保命核心）
    adaptive_grid: bool = True  # 强烈建议默认开启自适应！实盘保命的核心。
    target_grid_step: float = 0.2  # 目标步长：0.20 USDT (过滤微观噪音的甜点区)
    min_grid_size: int = 30  # 下限：防止大瀑布时点数过少，曲线变成多边形
    max_grid_size: int = 80  # 上限：熔断机制，死保 0.2ms 以内的极速延迟


@dataclass
class RiskManagerConfig:
    """风险管理器配置（小资金优化版）"""
    account_size_usdt: float = 300.0  # 账户规模（USDT）
    max_risk_per_trade_pct: float = 5.0  # 单笔交易最大风险百分比 (5%)
    stop_loss_ticks: int = 2  # 止损距离 (2 Tick)
    take_profit_ticks: int = 6  # 止盈距离 (6 Tick)
    max_daily_loss_pct: float = 5.0  # 单日最大损失百分比 (5%)


@dataclass
class TripleAEngineConfig:
    """四号引擎完整配置"""
    market: MarketConfig = field(default_factory=MarketConfig)
    data_pipeline: DataPipelineConfig = field(default_factory=DataPipelineConfig)
    range_bar: RangeBarConfig = field(default_factory=RangeBarConfig)
    kde_engine: KDEEngineConfig = field(default_factory=KDEEngineConfig)
    risk_manager: RiskManagerConfig = field(default_factory=RiskManagerConfig)

    # 性能优化配置
    enable_numba_cache: bool = True
    enable_background_warmup: bool = True
    enable_cpu_affinity: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化"""
        return {
            'market': self.market.to_dict() if hasattr(self.market, 'to_dict') else self.market.__dict__,
            'data_pipeline': self.data_pipeline.to_dict() if hasattr(self.data_pipeline,
                                                                     'to_dict') else self.data_pipeline.__dict__,
            'range_bar': self.range_bar.to_dict() if hasattr(self.range_bar, 'to_dict') else self.range_bar.__dict__,
            'kde_engine': self.kde_engine.to_dict() if hasattr(self.kde_engine,
                                                               'to_dict') else self.kde_engine.__dict__,
            'risk_manager': self.risk_manager.to_dict() if hasattr(self.risk_manager,
                                                                   'to_dict') else self.risk_manager.__dict__,
            'enable_numba_cache': self.enable_numba_cache,
            'enable_background_warmup': self.enable_background_warmup,
            'enable_cpu_affinity': self.enable_cpu_affinity
        }

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'TripleAEngineConfig':
        """从字典创建配置对象"""
        # 简单实现，实际中可能需要更复杂的解析
        return cls(**config_dict)


# 序列化辅助函数
def encode_dataclass(obj) -> Dict[str, Any]:
    """将dataclass对象编码为可序列化的字典"""
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()
    elif hasattr(obj, '__dict__'):
        return obj.__dict__
    else:
        raise ValueError(f"对象 {type(obj)} 不支持序列化")


def decode_dataclass(data: Dict[str, Any], cls) -> Any:
    """从字典解码为dataclass对象"""
    return cls(**data)


# 仓位状态数据结构
@dataclass
class PositionState:
    """仓位状态信息"""
    position_id: str  # 仓位ID
    symbol: str  # 交易对标识符，如 "ETH-USDT-SWAP"
    direction: str  # 仓位方向 "LONG" 或 "SHORT"
    entry_price: float  # 入场价格
    current_price: float  # 当前市场价格
    position_size: float  # 仓位大小（币数）
    entry_time: float  # 入场时间戳（秒）
    stop_loss_price: float  # 止损价格
    take_profit_price: float  # 止盈价格
    unrealized_pnl: float  # 未实现盈亏（USDT）
    realized_pnl: float  # 已实现盈亏（USDT）

    def __post_init__(self):
        """后初始化处理，确保数据一致性"""
        # 确保止损和止盈价格合理
        if self.direction.upper() == "LONG":
            if self.stop_loss_price > self.entry_price:
                self.stop_loss_price = self.entry_price * 0.99  # 默认1%止损
            if self.take_profit_price < self.entry_price:
                self.take_profit_price = self.entry_price * 1.02  # 默认2%止盈
        elif self.direction.upper() == "SHORT":
            if self.stop_loss_price < self.entry_price:
                self.stop_loss_price = self.entry_price * 1.01  # 默认1%止损
            if self.take_profit_price > self.entry_price:
                self.take_profit_price = self.entry_price * 0.98  # 默认2%止盈

    def get_pnl_percentage(self) -> float:
        """获取盈亏百分比"""
        if self.direction.upper() == "LONG":
            return ((self.current_price - self.entry_price) / self.entry_price) * 100.0
        else:  # SHORT
            return ((self.entry_price - self.current_price) / self.entry_price) * 100.0

    def get_pnl_usdt(self) -> float:
        """获取盈亏金额（USDT）"""
        if self.direction.upper() == "LONG":
            return (self.current_price - self.entry_price) * self.position_size
        else:  # SHORT
            return (self.entry_price - self.current_price) * self.position_size

    def is_stop_loss_triggered(self) -> bool:
        """检查止损是否触发"""
        if self.direction.upper() == "LONG":
            return self.current_price <= self.stop_loss_price
        else:  # SHORT
            return self.current_price >= self.stop_loss_price

    def is_take_profit_triggered(self) -> bool:
        """检查止盈是否触发"""
        if self.direction.upper() == "LONG":
            return self.current_price >= self.take_profit_price
        else:  # SHORT
            return self.current_price <= self.take_profit_price

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化"""
        return {
            'position_id': self.position_id,
            'symbol': self.symbol,
            'direction': self.direction,
            'entry_price': self.entry_price,
            'current_price': self.current_price,
            'position_size': self.position_size,
            'entry_time': self.entry_time,
            'stop_loss_price': self.stop_loss_price,
            'take_profit_price': self.take_profit_price,
            'unrealized_pnl': self.unrealized_pnl,
            'realized_pnl': self.realized_pnl,
            'pnl_percentage': self.get_pnl_percentage(),
            'pnl_usdt': self.get_pnl_usdt()
        }
