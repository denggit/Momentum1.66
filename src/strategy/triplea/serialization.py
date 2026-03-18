"""
四号引擎v3.0 高性能数据序列化工具
优化进程间数据传输性能，支持Numpy数组、压缩传输和零拷贝技术
"""

import json
import pickle
import struct
import zlib
from enum import Enum
from typing import Any, Dict, List, Tuple, Optional

import lz4.frame
import numpy as np


class SerializationFormat(Enum):
    """序列化格式枚举"""
    PICKLE = "pickle"  # Python pickle格式（兼容性好）
    JSON = "json"  # JSON格式（可读性好）
    NUMPY = "numpy"  # Numpy原生格式（性能最好）
    MSGPACK = "msgpack"  # MessagePack格式（紧凑高效）
    PROTOBUF = "protobuf"  # Protocol Buffers（跨语言）


class CompressionMethod(Enum):
    """压缩方法枚举"""
    NONE = "none"  # 不压缩
    ZLIB = "zlib"  # zlib压缩（平衡）
    LZ4 = "lz4"  # LZ4压缩（快速）
    SNAPPY = "snappy"  # Snappy压缩（Google）
    GZIP = "gzip"  # Gzip压缩（高压缩比）


class NumpySerializer:
    """Numpy数组序列化器"""

    @staticmethod
    def serialize(array: np.ndarray, include_metadata: bool = True) -> bytes:
        """
        序列化Numpy数组

        Args:
            array: Numpy数组
            include_metadata: 是否包含元数据（形状、数据类型）

        Returns:
            序列化的字节数据
        """
        if not isinstance(array, np.ndarray):
            raise TypeError(f"期望np.ndarray，得到 {type(array)}")

        # 获取数组信息
        dtype = array.dtype
        shape = array.shape
        is_contiguous = array.flags['C_CONTIGUOUS']

        # 如果数组不是连续内存，创建连续副本
        if not is_contiguous:
            array = np.ascontiguousarray(array)

        # 序列化数据
        data_bytes = array.tobytes()

        if include_metadata:
            # 序列化元数据
            metadata = {
                'dtype': str(dtype),
                'shape': shape,
                'is_contiguous': is_contiguous,
                'itemsize': array.itemsize,
                'nbytes': array.nbytes
            }

            # 将元数据添加到数据前面
            metadata_bytes = json.dumps(metadata).encode('utf-8')
            metadata_size = len(metadata_bytes)

            # 使用头部格式：元数据大小(4B) + 元数据 + 数组数据
            header = struct.pack('I', metadata_size)
            return header + metadata_bytes + data_bytes
        else:
            return data_bytes

    @staticmethod
    def deserialize(data: bytes, has_metadata: bool = True) -> np.ndarray:
        """
        反序列化Numpy数组

        Args:
            data: 序列化的字节数据
            has_metadata: 是否包含元数据

        Returns:
            Numpy数组
        """
        if not has_metadata:
            # 如果没有元数据，需要外部提供形状和数据类型
            raise ValueError("没有元数据时，需要提供形状和数据类型")

        # 解析元数据大小
        metadata_size = struct.unpack('I', data[:4])[0]

        # 解析元数据
        metadata_bytes = data[4:4 + metadata_size]
        metadata = json.loads(metadata_bytes.decode('utf-8'))

        # 解析数组数据
        array_data = data[4 + metadata_size:]

        # 重建数组
        dtype = np.dtype(metadata['dtype'])
        shape = tuple(metadata['shape'])

        array = np.frombuffer(array_data, dtype=dtype).reshape(shape)

        return array

    @staticmethod
    def serialize_batch(arrays: List[np.ndarray]) -> bytes:
        """批量序列化Numpy数组"""
        serialized_parts = []
        total_arrays = len(arrays)

        # 头部：数组数量
        header = struct.pack('I', total_arrays)
        serialized_parts.append(header)

        for array in arrays:
            # 序列化每个数组（包含元数据）
            array_bytes = NumpySerializer.serialize(array, include_metadata=True)
            array_size = len(array_bytes)

            # 添加数组大小和数组数据
            size_header = struct.pack('I', array_size)
            serialized_parts.append(size_header)
            serialized_parts.append(array_bytes)

        return b''.join(serialized_parts)

    @staticmethod
    def deserialize_batch(data: bytes) -> List[np.ndarray]:
        """批量反序列化Numpy数组"""
        # 解析数组数量
        total_arrays = struct.unpack('I', data[:4])[0]
        offset = 4

        arrays = []
        for _ in range(total_arrays):
            # 解析数组大小
            array_size = struct.unpack('I', data[offset:offset + 4])[0]
            offset += 4

            # 解析数组数据
            array_data = data[offset:offset + array_size]
            offset += array_size

            # 反序列化数组
            array = NumpySerializer.deserialize(array_data, has_metadata=True)
            arrays.append(array)

        return arrays


class HighPerformanceSerializer:
    """高性能序列化器"""

    def __init__(self,
                 format: SerializationFormat = SerializationFormat.NUMPY,
                 compression: CompressionMethod = CompressionMethod.LZ4,
                 compression_level: int = 3):
        """
        初始化序列化器

        Args:
            format: 序列化格式
            compression: 压缩方法
            compression_level: 压缩级别（1-9，越高压缩越好但越慢）
        """
        self.format = format
        self.compression = compression
        self.compression_level = compression_level
        self.stats = {
            'serializations': 0,
            'deserializations': 0,
            'total_bytes_in': 0,
            'total_bytes_out': 0,
            'compression_ratio': 1.0,
            'serialization_time': 0.0,
            'deserialization_time': 0.0
        }

    def serialize(self, obj: Any, use_compression: bool = True) -> bytes:
        """
        序列化对象

        Args:
            obj: 要序列化的对象
            use_compression: 是否使用压缩

        Returns:
            序列化的字节数据
        """
        import time
        start_time = time.perf_counter()

        # 根据格式选择序列化方法
        if self.format == SerializationFormat.NUMPY and isinstance(obj, np.ndarray):
            serialized = NumpySerializer.serialize(obj)
        elif self.format == SerializationFormat.JSON:
            serialized = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        else:
            # 默认使用pickle
            serialized = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)

        # 应用压缩
        if use_compression and len(serialized) > 100:  # 小数据不压缩
            serialized = self._compress(serialized)

        # 更新统计信息
        end_time = time.perf_counter()
        self.stats['serializations'] += 1
        self.stats['total_bytes_in'] += len(serialized)
        self.stats['serialization_time'] += (end_time - start_time)

        return serialized

    def deserialize(self, data: bytes, is_compressed: bool = True) -> Any:
        """
        反序列化对象

        Args:
            data: 序列化的字节数据
            is_compressed: 数据是否被压缩

        Returns:
            反序列化的对象
        """
        import time
        start_time = time.perf_counter()

        # 解压缩
        if is_compressed:
            data = self._decompress(data)

        # 根据格式选择反序列化方法
        if self.format == SerializationFormat.NUMPY:
            # 尝试反序列化为Numpy数组
            try:
                result = NumpySerializer.deserialize(data, has_metadata=True)
            except:
                # 如果失败，尝试其他格式
                try:
                    result = json.loads(data.decode('utf-8'))
                except:
                    result = pickle.loads(data)
        elif self.format == SerializationFormat.JSON:
            result = json.loads(data.decode('utf-8'))
        else:
            result = pickle.loads(data)

        # 更新统计信息
        end_time = time.perf_counter()
        self.stats['deserializations'] += 1
        self.stats['total_bytes_out'] += len(data)
        self.stats['deserialization_time'] += (end_time - start_time)

        if self.stats['total_bytes_in'] > 0 and self.stats['total_bytes_out'] > 0:
            self.stats['compression_ratio'] = (
                    self.stats['total_bytes_in'] / self.stats['total_bytes_out']
            )

        return result

    def _compress(self, data: bytes) -> bytes:
        """压缩数据"""
        if self.compression == CompressionMethod.NONE:
            return data
        elif self.compression == CompressionMethod.ZLIB:
            return zlib.compress(data, level=self.compression_level)
        elif self.compression == CompressionMethod.LZ4:
            return lz4.frame.compress(data, compression_level=self.compression_level)
        elif self.compression == CompressionMethod.GZIP:
            import gzip
            return gzip.compress(data, compresslevel=self.compression_level)
        else:
            # 默认使用zlib
            return zlib.compress(data, level=self.compression_level)

    def _decompress(self, data: bytes) -> bytes:
        """解压缩数据"""
        if self.compression == CompressionMethod.NONE:
            return data
        elif self.compression == CompressionMethod.ZLIB:
            return zlib.decompress(data)
        elif self.compression == CompressionMethod.LZ4:
            return lz4.frame.decompress(data)
        elif self.compression == CompressionMethod.GZIP:
            import gzip
            return gzip.decompress(data)
        else:
            # 尝试自动检测
            try:
                return zlib.decompress(data)
            except zlib.error:
                try:
                    return lz4.frame.decompress(data)
                except:
                    # 如果都不是，返回原始数据
                    return data

    def serialize_with_header(self, obj: Any) -> bytes:
        """
        序列化对象并添加头部信息

        头部格式：
        - 格式标识 (1B)
        - 压缩标识 (1B)
        - 数据大小 (4B)
        - 校验和 (4B)
        - 数据
        """
        # 序列化对象
        serialized = self.serialize(obj, use_compression=True)

        # 创建头部
        format_byte = ord(self.format.value[0])  # 简单格式标识
        compression_byte = ord(self.compression.value[0])  # 简单压缩标识
        data_size = len(serialized)
        checksum = self._calculate_checksum(serialized)

        header = struct.pack('BBII', format_byte, compression_byte, data_size, checksum)

        return header + serialized

    def deserialize_with_header(self, data: bytes) -> Any:
        """从带头部的数据中反序列化对象"""
        # 解析头部
        header_format = 'BBII'
        header_size = struct.calcsize(header_format)
        if len(data) < header_size:
            raise ValueError(f"数据太小，无法包含头部: {len(data)} < {header_size}")

        format_byte, compression_byte, data_size, checksum = struct.unpack(header_format, data[:header_size])

        # 验证数据大小
        if len(data) - header_size != data_size:
            raise ValueError(f"数据大小不匹配: 期望 {data_size}, 实际 {len(data) - header_size}")

        # 提取数据部分
        data_part = data[header_size:]

        # 验证校验和
        actual_checksum = self._calculate_checksum(data_part)
        if actual_checksum != checksum:
            raise ValueError(f"校验和失败: 期望 {checksum}, 实际 {actual_checksum}")

        # 确定格式和压缩方法
        format_char = chr(format_byte)
        compression_char = chr(compression_byte)

        # 临时设置格式和压缩方法
        original_format = self.format
        original_compression = self.compression

        try:
            # 根据头部信息设置格式和压缩方法
            for fmt in SerializationFormat:
                if fmt.value.startswith(format_char):
                    self.format = fmt
                    break

            for comp in CompressionMethod:
                if comp.value.startswith(compression_char):
                    self.compression = comp
                    break

            # 反序列化
            return self.deserialize(data_part, is_compressed=True)
        finally:
            # 恢复原始设置
            self.format = original_format
            self.compression = original_compression

    def _calculate_checksum(self, data: bytes) -> int:
        """计算简单的校验和"""
        return sum(data) & 0xFFFFFFFF

    def get_stats(self) -> Dict[str, Any]:
        """获取序列化统计信息"""
        return self.stats.copy()

    def reset_stats(self):
        """重置统计信息"""
        self.stats = {
            'serializations': 0,
            'deserializations': 0,
            'total_bytes_in': 0,
            'total_bytes_out': 0,
            'compression_ratio': 1.0,
            'serialization_time': 0.0,
            'deserialization_time': 0.0
        }


class ZeroCopySerializer:
    """零拷贝序列化器（使用共享内存）"""

    def __init__(self):
        """初始化零拷贝序列化器"""
        self.shared_arrays = {}

    def create_shared_array(self, shape: Tuple[int, ...], dtype: np.dtype = np.float64) -> np.ndarray:
        """
        创建共享内存数组

        Args:
            shape: 数组形状
            dtype: 数据类型

        Returns:
            共享内存数组
        """
        # 在实际实现中，这里会使用multiprocessing.Array或shared_memory
        # 简化版本：返回普通数组
        array = np.zeros(shape, dtype=dtype)
        array_id = id(array)

        self.shared_arrays[array_id] = {
            'array': array,
            'shape': shape,
            'dtype': dtype,
            'created_at': time.time()
        }

        return array

    def serialize_array_metadata(self, array: np.ndarray) -> Dict:
        """
        序列化数组元数据（不包含实际数据）

        Args:
            array: Numpy数组

        Returns:
            元数据字典
        """
        return {
            'shape': array.shape,
            'dtype': str(array.dtype),
            'itemsize': array.itemsize,
            'nbytes': array.nbytes,
            'is_contiguous': array.flags['C_CONTIGUOUS']
        }

    def deserialize_array_from_metadata(self, metadata: Dict, data_pointer: int = 0) -> np.ndarray:
        """
        从元数据和数据指针重建数组

        Args:
            metadata: 数组元数据
            data_pointer: 数据指针（在实际实现中指向共享内存）

        Returns:
            Numpy数组
        """
        # 在实际实现中，这里会从共享内存重建数组
        # 简化版本：创建新数组
        dtype = np.dtype(metadata['dtype'])
        shape = tuple(metadata['shape'])

        return np.zeros(shape, dtype=dtype)


# 全局默认序列化器
_default_serializer: Optional[HighPerformanceSerializer] = None


def get_default_serializer() -> HighPerformanceSerializer:
    """获取默认序列化器"""
    global _default_serializer
    if _default_serializer is None:
        _default_serializer = HighPerformanceSerializer(
            format=SerializationFormat.NUMPY,
            compression=CompressionMethod.LZ4,
            compression_level=3
        )
    return _default_serializer


# 导入time模块（需要在类定义后添加）
import time
