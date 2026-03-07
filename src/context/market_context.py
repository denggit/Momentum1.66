#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
市场上下文（MarketContext）
线程安全的情报池，用于替代危险的状态共享方式。

设计原则：
1. 单向数据流：DataFeed → Strategy → Context → Execution
2. 线程安全：所有访问通过锁保护
3. 最小化共享：只共享必要的情报，不共享业务逻辑状态
"""
import threading
import copy
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass, field
from datetime import datetime

from src.utils.log import get_logger

logger = get_logger(__name__)


@dataclass
class PositionInfo:
    """持仓信息数据类"""
    symbol: str = ""
    side: str = ""  # "long" or "short"
    size: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    leverage: int = 1
    entry_time: datetime = field(default_factory=datetime.now)

    # 止损相关
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    initial_stop_loss: float = 0.0

    # 生命周期状态
    stage: int = 0  # 4阶段止损的当前阶段
    stage_start_time: datetime = field(default_factory=datetime.now)
    stage_start_price: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于JSON序列化）"""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
            "leverage": self.leverage,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "initial_stop_loss": self.initial_stop_loss,
            "stage": self.stage,
            "stage_start_time": self.stage_start_time.isoformat() if self.stage_start_time else None,
            "stage_start_price": self.stage_start_price
        }


@dataclass
class SignalInfo:
    """交易信号数据类"""
    level: str = ""  # "STRICT", "BROAD", "REJECTED"
    price: float = 0.0
    local_low: float = 0.0
    cvd_delta_usdt: float = 0.0
    micro_cvd: float = 0.0
    price_diff_pct: float = 0.0
    effort_anomaly: float = 0.0
    res_anomaly: float = 0.0
    ts: float = 0.0

    # SMC验证结果
    smc_msg: str = ""
    smc_safe: bool = False
    smc_perfect: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于JSON序列化）"""
        return {
            "level": self.level,
            "price": self.price,
            "local_low": self.local_low,
            "cvd_delta_usdt": self.cvd_delta_usdt,
            "micro_cvd": self.micro_cvd,
            "price_diff_pct": self.price_diff_pct,
            "effort_anomaly": self.effort_anomaly,
            "res_anomaly": self.res_anomaly,
            "ts": self.ts,
            "smc_msg": self.smc_msg,
            "smc_safe": self.smc_safe,
            "smc_perfect": self.smc_perfect
        }


class MarketContext:
    """线程安全的市场情报池"""

    def __init__(self):
        self._lock = threading.RLock()
        self._reset()

    def _reset(self):
        """重置所有状态（内部使用）"""
        # 订单流情报
        self.of_wall_price = 0.0  # 订单流发现的隐形墙价格
        self.of_squeeze_flag = False  # 订单流拉响的空头挤压警报
        self.of_wall_ts = 0.0  # 隐形墙发现时间戳
        self.of_squeeze_ts = 0.0  # 空头挤压发现时间戳

        # SMC情报
        self.smc_support = 0.0  # SMC发现的支撑位
        self.smc_resistance = 0.0  # SMC发现的阻力位
        self.smc_update_ts = 0.0  # SMC最后更新时间

        # 持仓状态
        self.position_info: Optional[PositionInfo] = None

        # 信号状态
        self.last_signal: Optional[SignalInfo] = None
        self.signal_count = 0

        # 市场数据
        self.tick_info: Dict[str, Any] = {}
        self.last_tick_ts = 0.0

        # 系统状态
        self.is_in_position = False
        self.position_entry_ts = 0.0

    # ==================== 订单流情报 ====================

    def update_of_wall(self, price: float, timestamp: Optional[float] = None):
        """更新订单流隐形墙价格"""
        with self._lock:
            self.of_wall_price = price
            self.of_wall_ts = timestamp or self._current_timestamp()
            logger.debug(f"[MarketContext] 更新隐形墙价格: {price:.2f}")

    def update_of_squeeze(self, flag: bool, timestamp: Optional[float] = None):
        """更新订单流空头挤压标志"""
        with self._lock:
            self.of_squeeze_flag = flag
            self.of_squeeze_ts = timestamp or self._current_timestamp()
            if flag:
                logger.debug(f"[MarketContext] 设置空头挤压警报")

    def get_of_wall(self) -> float:
        """获取订单流隐形墙价格"""
        with self._lock:
            return self.of_wall_price

    def get_of_squeeze(self) -> bool:
        """获取订单流空头挤压标志"""
        with self._lock:
            return self.of_squeeze_flag

    def get_of_wall_age(self) -> float:
        """获取隐形墙情报的年龄（秒）"""
        with self._lock:
            if self.of_wall_ts == 0:
                return float('inf')
            return self._current_timestamp() - self.of_wall_ts

    def get_of_squeeze_age(self) -> float:
        """获取空头挤压情报的年龄（秒）"""
        with self._lock:
            if self.of_squeeze_ts == 0:
                return float('inf')
            return self._current_timestamp() - self.of_squeeze_ts

    # ==================== SMC情报 ====================

    def update_smc_levels(self, support: float, resistance: float,
                          timestamp: Optional[float] = None):
        """更新SMC支撑阻力位"""
        with self._lock:
            self.smc_support = support
            self.smc_resistance = resistance
            self.smc_update_ts = timestamp or self._current_timestamp()
            logger.debug(f"[MarketContext] 更新SMC水平: 支撑={support:.2f}, 阻力={resistance:.2f}")

    def get_smc_levels(self) -> Dict[str, float]:
        """获取SMC水平"""
        with self._lock:
            return {
                "support": self.smc_support,
                "resistance": self.smc_resistance
            }

    def get_smc_age(self) -> float:
        """获取SMC情报的年龄（秒）"""
        with self._lock:
            if self.smc_update_ts == 0:
                return float('inf')
            return self._current_timestamp() - self.smc_update_ts

    # ==================== 持仓管理 ====================

    def update_position(self, position_info: Union[PositionInfo, Dict[str, Any]]):
        """更新持仓信息"""
        with self._lock:
            if isinstance(position_info, dict):
                # 从字典创建PositionInfo
                pos = PositionInfo(
                    symbol=position_info.get("symbol", ""),
                    side=position_info.get("side", ""),
                    size=position_info.get("size", 0.0),
                    entry_price=position_info.get("entry_price", 0.0),
                    current_price=position_info.get("current_price", 0.0),
                    unrealized_pnl=position_info.get("unrealized_pnl", 0.0),
                    leverage=position_info.get("leverage", 1),
                    stop_loss_price=position_info.get("stop_loss_price", 0.0),
                    take_profit_price=position_info.get("take_profit_price", 0.0),
                    initial_stop_loss=position_info.get("initial_stop_loss", 0.0),
                    stage=position_info.get("stage", 0),
                    stage_start_price=position_info.get("stage_start_price", 0.0)
                )

                # 处理时间字段
                entry_time = position_info.get("entry_time")
                if entry_time:
                    if isinstance(entry_time, str):
                        pos.entry_time = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                    elif isinstance(entry_time, datetime):
                        pos.entry_time = entry_time

                stage_start_time = position_info.get("stage_start_time")
                if stage_start_time:
                    if isinstance(stage_start_time, str):
                        pos.stage_start_time = datetime.fromisoformat(stage_start_time.replace('Z', '+00:00'))
                    elif isinstance(stage_start_time, datetime):
                        pos.stage_start_time = stage_start_time

                self.position_info = pos
            else:
                self.position_info = position_info

            self.is_in_position = self.position_info is not None
            if self.is_in_position and self.position_entry_ts == 0:
                self.position_entry_ts = self._current_timestamp()

            logger.debug(f"[MarketContext] 更新持仓: {self.position_info}")

    def clear_position(self):
        """清空持仓信息"""
        with self._lock:
            self.position_info = None
            self.is_in_position = False
            self.position_entry_ts = 0
            logger.debug("[MarketContext] 清空持仓")

    def get_position(self) -> Optional[PositionInfo]:
        """获取持仓信息"""
        with self._lock:
            return copy.deepcopy(self.position_info) if self.position_info else None

    def get_position_dict(self) -> Optional[Dict[str, Any]]:
        """获取持仓信息（字典形式）"""
        with self._lock:
            return self.position_info.to_dict() if self.position_info else None

    # ==================== 信号管理 ====================

    def update_signal(self, signal_info: Union[SignalInfo, Dict[str, Any]]):
        """更新交易信号"""
        with self._lock:
            if isinstance(signal_info, dict):
                sig = SignalInfo(
                    level=signal_info.get("level", ""),
                    price=signal_info.get("price", 0.0),
                    local_low=signal_info.get("local_low", 0.0),
                    cvd_delta_usdt=signal_info.get("cvd_delta_usdt", 0.0),
                    micro_cvd=signal_info.get("micro_cvd", 0.0),
                    price_diff_pct=signal_info.get("price_diff_pct", 0.0),
                    effort_anomaly=signal_info.get("effort_anomaly", 0.0),
                    res_anomaly=signal_info.get("res_anomaly", 0.0),
                    ts=signal_info.get("ts", 0.0),
                    smc_msg=signal_info.get("smc_msg", ""),
                    smc_safe=signal_info.get("smc_safe", False),
                    smc_perfect=signal_info.get("smc_perfect", False)
                )
                self.last_signal = sig
            else:
                self.last_signal = signal_info

            self.signal_count += 1
            logger.debug(f"[MarketContext] 更新信号: {self.last_signal}")

    def get_signal(self) -> Optional[SignalInfo]:
        """获取最后交易信号"""
        with self._lock:
            return copy.deepcopy(self.last_signal) if self.last_signal else None

    def get_signal_dict(self) -> Optional[Dict[str, Any]]:
        """获取最后交易信号（字典形式）"""
        with self._lock:
            return self.last_signal.to_dict() if self.last_signal else None

    # ==================== 市场数据 ====================

    def update_tick(self, tick: Dict[str, Any]):
        """更新Tick数据"""
        with self._lock:
            self.tick_info = copy.deepcopy(tick)
            self.last_tick_ts = tick.get('ts', 0.0)

            # 如果持仓存在，更新当前价格
            if self.position_info:
                current_price = tick.get('price', 0.0)
                if current_price > 0:
                    self.position_info.current_price = current_price

    def get_tick(self) -> Dict[str, Any]:
        """获取最新Tick数据"""
        with self._lock:
            return copy.deepcopy(self.tick_info)

    def get_current_price(self) -> float:
        """获取当前市场价格"""
        with self._lock:
            return self.tick_info.get('price', 0.0) if self.tick_info else 0.0

    def get_last_tick_ts(self) -> float:
        """获取最新Tick的时间戳"""
        with self._lock:
            return self.last_tick_ts

    def get_tick_age(self) -> float:
        """获取Tick数据的年龄（秒）"""
        with self._lock:
            if self.last_tick_ts == 0:
                return float('inf')
            return self._current_timestamp() - self.last_tick_ts

    # ==================== 快照与诊断 ====================

    def get_snapshot(self) -> Dict[str, Any]:
        """获取当前上下文的快照（用于日志和诊断）"""
        with self._lock:
            snapshot = {
                # 订单流情报
                "of_wall_price": self.of_wall_price,
                "of_squeeze_flag": self.of_squeeze_flag,
                "of_wall_age": self.get_of_wall_age(),
                "of_squeeze_age": self.get_of_squeeze_age(),

                # SMC情报
                "smc_support": self.smc_support,
                "smc_resistance": self.smc_resistance,
                "smc_age": self.get_smc_age(),

                # 持仓状态
                "has_position": self.is_in_position,
                "position_entry_age": self._current_timestamp() - self.position_entry_ts if self.position_entry_ts > 0 else 0,
                "position_stage": self.position_info.stage if self.position_info else 0,

                # 信号状态
                "signal_count": self.signal_count,
                "last_signal_level": self.last_signal.level if self.last_signal else None,
                "last_signal_ts": self.last_signal.ts if self.last_signal else 0,

                # 市场数据
                "current_price": self.get_current_price(),
                "tick_age": self.get_tick_age(),

                # 系统状态
                "timestamp": self._current_timestamp()
            }

            # 添加持仓详细信息（如果有）
            if self.position_info:
                snapshot["position_details"] = {
                    "symbol": self.position_info.symbol,
                    "side": self.position_info.side,
                    "size": self.position_info.size,
                    "entry_price": self.position_info.entry_price,
                    "unrealized_pnl": self.position_info.unrealized_pnl,
                    "stop_loss_price": self.position_info.stop_loss_price,
                    "take_profit_price": self.position_info.take_profit_price
                }

            return snapshot

    def log_snapshot(self, level: str = "info"):
        """记录上下文快照到日志"""
        snapshot = self.get_snapshot()
        log_method = getattr(logger, level.lower(), logger.info)

        log_method(f"[MarketContext] 上下文快照:")
        log_method(f"  订单流: 墙={snapshot['of_wall_price']:.2f}({snapshot['of_wall_age']:.1f}s), "
                  f"挤压={snapshot['of_squeeze_flag']}({snapshot['of_squeeze_age']:.1f}s)")
        log_method(f"  SMC: 支撑={snapshot['smc_support']:.2f}, 阻力={snapshot['smc_resistance']:.2f} "
                  f"({snapshot['smc_age']:.1f}s)")
        log_method(f"  持仓: {'有' if snapshot['has_position'] else '无'}, "
                  f"阶段={snapshot['position_stage']}, 持有={snapshot['position_entry_age']:.1f}s")
        log_method(f"  信号: 总数={snapshot['signal_count']}, 最后={snapshot['last_signal_level']}")
        log_method(f"  市场: 现价={snapshot['current_price']:.2f}, Tick年龄={snapshot['tick_age']:.3f}s")

    # ==================== 辅助方法 ====================

    def _current_timestamp(self) -> float:
        """获取当前时间戳（便于测试）"""
        import time
        return time.time()

    def reset(self):
        """重置所有状态（公开方法）"""
        with self._lock:
            self._reset()
            logger.info("[MarketContext] 重置所有状态")

    def is_fresh(self, max_age: float = 30.0) -> bool:
        """检查上下文数据是否新鲜（所有数据都在max_age秒内）"""
        with self._lock:
            ages = [
                self.get_tick_age(),
                self.get_smc_age(),
                self.get_of_wall_age(),
                self.get_of_squeeze_age()
            ]
            return all(age <= max_age for age in ages)

    def get_stats(self) -> Dict[str, Any]:
        """获取上下文统计信息"""
        with self._lock:
            return {
                "signal_count": self.signal_count,
                "position_duration": self._current_timestamp() - self.position_entry_ts if self.position_entry_ts > 0 else 0,
                "data_freshness": self.is_fresh(),
                "last_update": self.last_tick_ts,
                "has_position": self.is_in_position,
                "has_smc_levels": self.smc_support > 0 and self.smc_resistance > 0
            }