"""
四号引擎v3.0 IPC通信协议
ProcessPoolExecutor进程间通信协议定义
"""

import enum
import json
import pickle
import struct
import zlib
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np


class MessageType(enum.IntEnum):
    """消息类型枚举"""
    # 控制消息
    TASK_REQUEST = 1  # 任务请求
    TASK_RESULT = 2  # 任务结果
    TASK_ERROR = 3  # 任务错误
    HEARTBEAT = 4  # 心跳检测
    SHUTDOWN = 5  # 关闭指令

    # 数据消息
    TICK_DATA = 10  # Tick数据
    RANGEBAR_DATA = 11  # RangeBar数据
    CVD_DATA = 12  # CVD数据
    KDE_REQUEST = 13  # KDE计算请求
    KDE_RESULT = 14  # KDE计算结果
    LVN_DATA = 15  # LVN数据

    # 状态消息
    STATUS_UPDATE = 20  # 状态更新
    METRICS_REPORT = 21  # 指标报告
    ERROR_REPORT = 22  # 错误报告


class TaskPriority(enum.IntEnum):
    """任务优先级枚举"""
    REALTIME = 0  # 实时任务（Tick处理）
    HIGH = 1  # 高优先级（CVD计算）
    NORMAL = 2  # 正常优先级（KDE计算）
    LOW = 3  # 低优先级（历史数据分析）
    BACKGROUND = 4  # 后台任务（日志处理）


@dataclass
class MessageHeader:
    """消息头部"""
    message_type: MessageType
    message_id: int = 0
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    priority: TaskPriority = TaskPriority.NORMAL
    source_pid: int = 0
    target_pid: int = 0
    data_size: int = 0
    checksum: int = 0
    compression: bool = False
    version: str = "1.0.0"

    def to_bytes(self) -> bytes:
        """将消息头转换为字节流"""
        # 使用固定格式：类型(1B) + ID(4B) + 时间戳(8B) + 优先级(1B) + 源PID(4B) + 目标PID(4B) + 数据大小(4B) + 校验和(4B) + 压缩标志(1B) + 版本(10B)
        version_bytes = self.version.ljust(10).encode('utf-8')[:10]

        header_format = 'B I d B I I I I B 10s'
        return struct.pack(
            header_format,
            self.message_type.value,
            self.message_id,
            self.timestamp,
            self.priority.value,
            self.source_pid,
            self.target_pid,
            self.data_size,
            self.checksum,
            1 if self.compression else 0,
            version_bytes
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> 'MessageHeader':
        """从字节流解析消息头"""
        # 计算头部大小
        header_format = 'B I d B I I I I B 10s'
        header_size = struct.calcsize(header_format)

        if len(data) < header_size:
            raise ValueError(f"消息头数据不足: {len(data)} 字节，需要 {header_size} 字节")

        (msg_type, msg_id, timestamp, priority,
         source_pid, target_pid, data_size,
         checksum, compression_flag, version_bytes) = struct.unpack(header_format, data[:header_size])

        version = version_bytes.decode('utf-8').strip()

        return cls(
            message_type=MessageType(msg_type),
            message_id=msg_id,
            timestamp=timestamp,
            priority=TaskPriority(priority),
            source_pid=source_pid,
            target_pid=target_pid,
            data_size=data_size,
            checksum=checksum,
            compression=bool(compression_flag),
            version=version
        )


@dataclass
class IPCMessage:
    """IPC消息完整结构"""
    header: MessageHeader
    data: Any = None

    def serialize(self) -> bytes:
        """序列化完整消息"""
        # 序列化数据
        if isinstance(self.data, np.ndarray):
            # Numpy数组特殊处理
            data_bytes = self._serialize_numpy(self.data)
        elif isinstance(self.data, (dict, list, tuple, str, int, float, bool, type(None))):
            # 使用JSON序列化
            data_bytes = json.dumps(self.data, ensure_ascii=False).encode('utf-8')
        else:
            # 使用pickle作为后备
            data_bytes = pickle.dumps(self.data, protocol=pickle.HIGHEST_PROTOCOL)

        # 压缩数据（如果启用）
        if self.header.compression and len(data_bytes) > 100:
            data_bytes = zlib.compress(data_bytes, level=3)

        # 计算校验和（压缩后）
        checksum = self._calculate_checksum(data_bytes)

        # 更新头部信息
        self.header.data_size = len(data_bytes)
        self.header.checksum = checksum

        # 组合头部和数据
        header_bytes = self.header.to_bytes()
        return header_bytes + data_bytes

    def deserialize(self, data: bytes) -> Any:
        """反序列化数据"""
        # 先解析头部获取头部大小
        import struct
        header_format = 'B I d B I I I I B 10s'
        header_size = struct.calcsize(header_format)

        # 解析头部
        self.header = MessageHeader.from_bytes(data[:header_size])

        # 提取数据部分
        data_bytes = data[header_size:header_size + self.header.data_size]

        # 验证校验和
        if self.header.checksum != 0:  # 0表示不校验
            actual_checksum = self._calculate_checksum(data_bytes)
            if actual_checksum != self.header.checksum:
                raise ValueError(f"校验和失败: 期望 {self.header.checksum}, 实际 {actual_checksum}")

        # 解压缩数据（如果启用）
        if self.header.compression:
            try:
                data_bytes = zlib.decompress(data_bytes)
            except zlib.error as e:
                raise ValueError(f"解压缩失败: {e}")

        # 反序列化数据
        try:
            # 先尝试JSON
            return json.loads(data_bytes.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            try:
                # 再尝试pickle
                return pickle.loads(data_bytes)
            except pickle.UnpicklingError:
                # 最后尝试Numpy
                try:
                    return self._deserialize_numpy(data_bytes)
                except:
                    raise ValueError("无法反序列化数据")

    def _serialize_numpy(self, array: np.ndarray) -> bytes:
        """序列化Numpy数组"""
        # 使用Numpy内置的序列化
        return array.tobytes()

    def _deserialize_numpy(self, data: bytes) -> np.ndarray:
        """反序列化Numpy数组"""
        # 这里需要知道数组的形状和数据类型
        # 在实际实现中，需要在数据中包含这些信息
        # 简化版本：假设是一维float64数组
        return np.frombuffer(data, dtype=np.float64)

    def _calculate_checksum(self, data: bytes) -> int:
        """计算简单的校验和"""
        return sum(data) & 0xFFFFFFFF  # 32位校验和


@dataclass
class TaskRequest:
    """任务请求消息"""
    task_id: str
    task_type: str
    data: Any
    priority: TaskPriority = TaskPriority.NORMAL
    timeout_seconds: float = 30.0
    retry_count: int = 0
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_message(self) -> IPCMessage:
        """转换为IPC消息"""
        header = MessageHeader(
            message_type=MessageType.TASK_REQUEST,
            priority=self.priority,
            data_size=0  # 将在序列化时设置
        )

        return IPCMessage(header=header, data=asdict(self))


@dataclass
class TaskResult:
    """任务结果消息"""
    task_id: str
    result: Any
    error: Optional[str] = None
    execution_time: float = 0.0
    completed_at: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_message(self) -> IPCMessage:
        """转换为IPC消息"""
        header = MessageHeader(
            message_type=MessageType.TASK_RESULT,
            priority=TaskPriority.NORMAL,
            data_size=0
        )

        return IPCMessage(header=header, data=asdict(self))


@dataclass
class TickData:
    """Tick数据消息"""
    price: float
    size: float
    side: str  # 'buy' or 'sell'
    timestamp: int  # Unix毫秒时间戳
    symbol: str = "ETH-USDT-SWAP"
    sequence: int = 0

    def to_message(self) -> IPCMessage:
        """转换为IPC消息"""
        header = MessageHeader(
            message_type=MessageType.TICK_DATA,
            priority=TaskPriority.REALTIME,
            data_size=0
        )

        return IPCMessage(header=header, data=asdict(self))


@dataclass
class RangeBarData:
    """RangeBar数据消息"""
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int
    bar_size: float = 1.0  # Range大小
    tick_count: int = 0

    def to_message(self) -> IPCMessage:
        """转换为IPC消息"""
        header = MessageHeader(
            message_type=MessageType.RANGEBAR_DATA,
            priority=TaskPriority.HIGH,
            data_size=0
        )

        return IPCMessage(header=header, data=asdict(self))


@dataclass
class KDERequest:
    """KDE计算请求"""
    request_id: str
    prices: np.ndarray
    bandwidth: float = 0.5
    grid_size: int = 100
    cache_key: Optional[str] = None

    def to_message(self) -> IPCMessage:
        """转换为IPC消息"""
        # 将numpy数组转换为可序列化的列表
        data = {
            'request_id': self.request_id,
            'prices': self.prices.tolist(),
            'bandwidth': self.bandwidth,
            'grid_size': self.grid_size,
            'cache_key': self.cache_key
        }

        header = MessageHeader(
            message_type=MessageType.KDE_REQUEST,
            priority=TaskPriority.NORMAL,
            data_size=0
        )

        return IPCMessage(header=header, data=data)


@dataclass
class KDEResult:
    """KDE计算结果"""
    request_id: str
    kde_values: np.ndarray
    grid_points: np.ndarray
    computation_time: float
    cache_hit: bool = False

    def to_message(self) -> IPCMessage:
        """转换为IPC消息"""
        data = {
            'request_id': self.request_id,
            'kde_values': self.kde_values.tolist(),
            'grid_points': self.grid_points.tolist(),
            'computation_time': self.computation_time,
            'cache_hit': self.cache_hit
        }

        header = MessageHeader(
            message_type=MessageType.KDE_RESULT,
            priority=TaskPriority.NORMAL,
            data_size=0
        )

        return IPCMessage(header=header, data=data)


class IPCProtocol:
    """IPC协议管理器"""

    def __init__(self, compression_threshold: int = 1024):
        """
        初始化IPC协议

        Args:
            compression_threshold: 压缩阈值（字节），大于此值的数据将被压缩
        """
        self.compression_threshold = compression_threshold
        self.message_counter = 0
        self.stats = {
            'messages_sent': 0,
            'messages_received': 0,
            'bytes_sent': 0,
            'bytes_received': 0,
            'compression_savings': 0
        }

    def create_message(self, message_type: MessageType, data: Any,
                       priority: TaskPriority = TaskPriority.NORMAL,
                       compress: bool = True) -> IPCMessage:
        """创建消息"""
        self.message_counter += 1

        # 自动决定是否压缩
        should_compress = compress
        if isinstance(data, dict):
            # 估算数据大小
            data_str = json.dumps(data)
            if len(data_str) < self.compression_threshold:
                should_compress = False

        header = MessageHeader(
            message_type=message_type,
            message_id=self.message_counter,
            priority=priority,
            source_pid=os.getpid(),
            compression=should_compress
        )

        return IPCMessage(header=header, data=data)

    def encode_message(self, message: IPCMessage) -> bytes:
        """编码消息为字节流"""
        serialized = message.serialize()

        # 更新统计信息
        self.stats['messages_sent'] += 1
        self.stats['bytes_sent'] += len(serialized)

        return serialized

    def decode_message(self, data: bytes) -> IPCMessage:
        """从字节流解码消息"""
        # 创建消息对象
        message = IPCMessage(header=None, data=None)

        # 反序列化数据
        message.data = message.deserialize(data)

        # 更新统计信息
        self.stats['messages_received'] += 1
        self.stats['bytes_received'] += len(data)

        return message

    def create_task_request(self, task_type: str, task_data: Any,
                            priority: TaskPriority = TaskPriority.NORMAL,
                            timeout_seconds: float = 30.0) -> bytes:
        """创建任务请求消息"""
        task_id = f"task_{self.message_counter}_{int(datetime.now().timestamp() * 1000)}"

        request = TaskRequest(
            task_id=task_id,
            task_type=task_type,
            data=task_data,
            priority=priority,
            timeout_seconds=timeout_seconds
        )

        message = request.to_message()
        return self.encode_message(message)

    def create_kde_request(self, prices: np.ndarray, bandwidth: float = 0.5,
                           grid_size: int = 100) -> bytes:
        """创建KDE计算请求消息"""
        request_id = f"kde_{self.message_counter}_{int(datetime.now().timestamp() * 1000)}"

        request = KDERequest(
            request_id=request_id,
            prices=prices,
            bandwidth=bandwidth,
            grid_size=grid_size
        )

        message = request.to_message()
        return self.encode_message(message)

    def get_stats(self) -> Dict[str, Any]:
        """获取协议统计信息"""
        return self.stats.copy()


# 导入os模块（需要在类定义后添加）
import os

# 全局IPC协议实例
_default_protocol: Optional[IPCProtocol] = None


def get_default_protocol() -> IPCProtocol:
    """获取默认IPC协议实例"""
    global _default_protocol
    if _default_protocol is None:
        _default_protocol = IPCProtocol()
    return _default_protocol
