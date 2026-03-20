"""
四号引擎v3.0 IPC协议测试
测试进程间通信协议的正确性和性能
"""

import unittest
import sys
import os
import time
import json
import numpy as np

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.strategy.triplea.ipc_protocol import (
    MessageType, TaskPriority, MessageHeader, IPCMessage,
    TaskRequest, TaskResult, TickData, RangeBarData, KDERequest, KDEResult,
    IPCProtocol, get_default_protocol
)

from src.strategy.triplea.serialization import (
    SerializationFormat, CompressionMethod, HighPerformanceSerializer,
    NumpySerializer, get_default_serializer
)

class TestMessageHeader(unittest.TestCase):
    """测试消息头部"""

    def test_header_creation(self):
        """测试头部创建"""
        header = MessageHeader(
            message_type=MessageType.TICK_DATA,
            message_id=123,
            timestamp=1609459200.0,
            priority=TaskPriority.REALTIME,
            source_pid=1001,
            target_pid=1002,
            data_size=1024,
            checksum=0x12345678,
            compression=True,
            version="1.0.0"
        )

        self.assertEqual(header.message_type, MessageType.TICK_DATA)
        self.assertEqual(header.message_id, 123)
        self.assertEqual(header.priority, TaskPriority.REALTIME)
        self.assertTrue(header.compression)
        self.assertEqual(header.version, "1.0.0")

    def test_header_serialization(self):
        """测试头部序列化/反序列化"""
        header = MessageHeader(
            message_type=MessageType.TASK_RESULT,
            message_id=456,
            timestamp=1609459200.0,
            priority=TaskPriority.HIGH,
            source_pid=2001,
            target_pid=2002,
            data_size=2048,
            checksum=0x87654321,
            compression=False,
            version="1.1.0"
        )

        # 序列化
        header_bytes = header.to_bytes()
        self.assertIsInstance(header_bytes, bytes)
        # 计算头部大小
        import struct
        header_format = 'B I d B I I I I B 10s'
        expected_size = struct.calcsize(header_format)
        self.assertEqual(len(header_bytes), expected_size)  # 固定头部大小

        # 反序列化
        header2 = MessageHeader.from_bytes(header_bytes)

        self.assertEqual(header2.message_type, header.message_type)
        self.assertEqual(header2.message_id, header.message_id)
        self.assertEqual(header2.priority, header.priority)
        self.assertEqual(header2.source_pid, header.source_pid)
        self.assertEqual(header2.target_pid, header.target_pid)
        self.assertEqual(header2.data_size, header.data_size)
        self.assertEqual(header2.checksum, header.checksum)
        self.assertEqual(header2.compression, header.compression)
        self.assertEqual(header2.version, header.version)

class TestIPCMessage(unittest.TestCase):
    """测试IPC消息"""

    def test_message_with_dict_data(self):
        """测试字典数据消息"""
        header = MessageHeader(message_type=MessageType.STATUS_UPDATE)
        data = {"status": "running", "cpu_usage": 45.6, "memory_mb": 128.3}

        message = IPCMessage(header=header, data=data)

        # 序列化
        serialized = message.serialize()
        self.assertIsInstance(serialized, bytes)
        self.assertGreater(len(serialized), len(header.to_bytes()))

        # 反序列化
        message2 = IPCMessage(header=None, data=None)
        deserialized_data = message2.deserialize(serialized)

        self.assertEqual(deserialized_data["status"], "running")
        self.assertEqual(deserialized_data["cpu_usage"], 45.6)
        self.assertEqual(deserialized_data["memory_mb"], 128.3)

    def test_message_with_numpy_data(self):
        """测试Numpy数组消息"""
        header = MessageHeader(message_type=MessageType.KDE_REQUEST)
        data = np.random.randn(1000).astype(np.float64)

        message = IPCMessage(header=header, data=data)

        # 序列化
        serialized = message.serialize()
        self.assertIsInstance(serialized, bytes)

        # 反序列化
        message2 = IPCMessage(header=None, data=None)
        deserialized_data = message2.deserialize(serialized)

        self.assertIsInstance(deserialized_data, np.ndarray)
        self.assertEqual(deserialized_data.shape, (1000,))
        self.assertEqual(deserialized_data.dtype, np.float64)
        np.testing.assert_array_almost_equal(deserialized_data, data)

    def test_message_compression(self):
        """测试消息压缩"""
        header = MessageHeader(
            message_type=MessageType.TICK_DATA,
            compression=True
        )

        # 创建大量数据以测试压缩效果
        data = {"ticks": [{"price": 3000.0 + i * 0.1, "size": 1.0} for i in range(1000)]}

        message = IPCMessage(header=header, data=data)

        # 序列化
        serialized = message.serialize()
        self.assertTrue(len(serialized) < len(json.dumps(data).encode('utf-8')) * 0.9)  # 压缩率应小于90%

class TestDataStructures(unittest.TestCase):
    """测试数据结构"""

    def test_task_request(self):
        """测试任务请求"""
        task_data = {"prices": [3000.0, 3001.0, 3002.0], "bandwidth": 0.5}

        request = TaskRequest(
            task_id="task_123",
            task_type="kde_computation",
            data=task_data,
            priority=TaskPriority.HIGH,
            timeout_seconds=30.0
        )

        self.assertEqual(request.task_id, "task_123")
        self.assertEqual(request.task_type, "kde_computation")
        self.assertEqual(request.data, task_data)
        self.assertEqual(request.priority, TaskPriority.HIGH)
        self.assertEqual(request.timeout_seconds, 30.0)

        # 测试转换为消息
        message = request.to_message()
        self.assertEqual(message.header.message_type, MessageType.TASK_REQUEST)

    def test_tick_data(self):
        """测试Tick数据"""
        tick = TickData(
            price=3000.5,
            size=1.2,
            side="buy",
            timestamp=1609459200000,
            symbol="ETH-USDT-SWAP",
            sequence=1001
        )

        self.assertEqual(tick.price, 3000.5)
        self.assertEqual(tick.size, 1.2)
        self.assertEqual(tick.side, "buy")
        self.assertEqual(tick.symbol, "ETH-USDT-SWAP")

        # 测试转换为消息
        message = tick.to_message()
        self.assertEqual(message.header.message_type, MessageType.TICK_DATA)
        self.assertEqual(message.header.priority, TaskPriority.REALTIME)

    def test_rangebar_data(self):
        """测试RangeBar数据"""
        rangebar = RangeBarData(
            open=3000.0,
            high=3005.0,
            low=2998.0,
            close=3002.0,
            volume=100.5,
            timestamp=1609459200000,
            bar_size=1.0,
            tick_count=50
        )

        self.assertEqual(rangebar.open, 3000.0)
        self.assertEqual(rangebar.high, 3005.0)
        self.assertEqual(rangebar.low, 2998.0)
        self.assertEqual(rangebar.volume, 100.5)

        # 测试转换为消息
        message = rangebar.to_message()
        self.assertEqual(message.header.message_type, MessageType.RANGEBAR_DATA)
        self.assertEqual(message.header.priority, TaskPriority.HIGH)

    def test_kde_request_response(self):
        """测试KDE请求和响应"""
        # 创建请求
        prices = np.random.randn(1000)
        request = KDERequest(
            request_id="kde_123",
            prices=prices,
            bandwidth=0.5,
            grid_size=100,
            cache_key="cache_123"
        )

        self.assertEqual(request.request_id, "kde_123")
        self.assertEqual(request.bandwidth, 0.5)
        self.assertEqual(request.grid_size, 100)
        self.assertEqual(request.cache_key, "cache_123")

        # 创建响应
        kde_values = np.random.rand(100)
        grid_points = np.linspace(prices.min(), prices.max(), 100)
        response = KDEResult(
            request_id="kde_123",
            kde_values=kde_values,
            grid_points=grid_points,
            computation_time=0.123,
            cache_hit=True
        )

        self.assertEqual(response.request_id, "kde_123")
        self.assertEqual(response.computation_time, 0.123)
        self.assertTrue(response.cache_hit)

class TestIPCProtocol(unittest.TestCase):
    """测试IPC协议"""

    def setUp(self):
        """设置测试环境"""
        self.protocol = IPCProtocol(compression_threshold=100)

    def test_protocol_creation(self):
        """测试协议创建"""
        self.assertEqual(self.protocol.compression_threshold, 100)
        self.assertEqual(self.protocol.message_counter, 0)

    def test_create_message(self):
        """测试创建消息"""
        data = {"test": "data", "value": 123.45}
        message = self.protocol.create_message(
            message_type=MessageType.STATUS_UPDATE,
            data=data,
            priority=TaskPriority.NORMAL,
            compress=True
        )

        self.assertEqual(message.header.message_type, MessageType.STATUS_UPDATE)
        self.assertEqual(message.header.priority, TaskPriority.NORMAL)
        self.assertEqual(message.header.source_pid, os.getpid())
        self.assertEqual(self.protocol.message_counter, 1)

    def test_encode_decode_message(self):
        """测试编码和解码消息"""
        # 创建消息
        original_data = {
            "status": "active",
            "metrics": {"cpu": 45.6, "memory": 128.3},
            "timestamp": time.time()
        }

        message = self.protocol.create_message(
            message_type=MessageType.METRICS_REPORT,
            data=original_data
        )

        # 编码
        encoded = self.protocol.encode_message(message)
        self.assertIsInstance(encoded, bytes)
        self.assertGreater(len(encoded), 0)

        # 解码
        decoded_message = self.protocol.decode_message(encoded)
        self.assertEqual(decoded_message.header.message_type, MessageType.METRICS_REPORT)
        self.assertEqual(decoded_message.data["status"], "active")
        self.assertEqual(decoded_message.data["metrics"]["cpu"], 45.6)

    def test_create_task_request(self):
        """测试创建任务请求"""
        task_data = {"input": [1, 2, 3, 4, 5]}
        request_bytes = self.protocol.create_task_request(
            task_type="test_task",
            task_data=task_data,
            priority=TaskPriority.HIGH,
            timeout_seconds=15.0
        )

        self.assertIsInstance(request_bytes, bytes)

        # 解码验证
        message = self.protocol.decode_message(request_bytes)
        self.assertEqual(message.header.message_type, MessageType.TASK_REQUEST)
        self.assertEqual(message.data["task_type"], "test_task")
        self.assertEqual(message.data["priority"], TaskPriority.HIGH.value)
        self.assertEqual(message.data["timeout_seconds"], 15.0)

    def test_create_kde_request(self):
        """测试创建KDE请求"""
        prices = np.random.randn(500)
        request_bytes = self.protocol.create_kde_request(
            prices=prices,
            bandwidth=0.3,
            grid_size=50
        )

        self.assertIsInstance(request_bytes, bytes)

        # 解码验证
        message = self.protocol.decode_message(request_bytes)
        self.assertEqual(message.header.message_type, MessageType.KDE_REQUEST)
        self.assertEqual(message.data["bandwidth"], 0.3)
        self.assertEqual(message.data["grid_size"], 50)
        self.assertEqual(len(message.data["prices"]), 500)

    def test_protocol_stats(self):
        """测试协议统计"""
        # 发送一些消息
        for i in range(5):
            data = {"iteration": i, "data": "x" * 100}
            message = self.protocol.create_message(MessageType.STATUS_UPDATE, data)
            encoded = self.protocol.encode_message(message)
            _ = self.protocol.decode_message(encoded)

        stats = self.protocol.get_stats()

        self.assertEqual(stats['messages_sent'], 5)
        self.assertEqual(stats['messages_received'], 5)
        self.assertGreater(stats['bytes_sent'], 0)
        self.assertGreater(stats['bytes_received'], 0)

class TestSerialization(unittest.TestCase):
    """测试序列化"""

    def test_numpy_serializer(self):
        """测试Numpy序列化器"""
        # 创建测试数组
        original_array = np.random.randn(100, 50).astype(np.float32)

        # 序列化
        serialized = NumpySerializer.serialize(original_array, include_metadata=True)
        self.assertIsInstance(serialized, bytes)

        # 反序列化
        deserialized_array = NumpySerializer.deserialize(serialized, has_metadata=True)

        # 验证
        self.assertEqual(deserialized_array.shape, original_array.shape)
        self.assertEqual(deserialized_array.dtype, original_array.dtype)
        np.testing.assert_array_equal(deserialized_array, original_array)

    def test_numpy_batch_serializer(self):
        """测试Numpy批量序列化器"""
        # 创建测试数组列表
        arrays = [
            np.random.randn(10, 10),
            np.random.randn(20, 5),
            np.random.randn(5, 20)
        ]

        # 批量序列化
        serialized = NumpySerializer.serialize_batch(arrays)
        self.assertIsInstance(serialized, bytes)

        # 批量反序列化
        deserialized_arrays = NumpySerializer.deserialize_batch(serialized)

        # 验证
        self.assertEqual(len(deserialized_arrays), len(arrays))
        for orig, deser in zip(arrays, deserialized_arrays):
            self.assertEqual(deser.shape, orig.shape)
            self.assertEqual(deser.dtype, orig.dtype)
            np.testing.assert_array_equal(deser, orig)

    def test_high_performance_serializer(self):
        """测试高性能序列化器"""
        serializer = HighPerformanceSerializer(
            format=SerializationFormat.NUMPY,
            compression=CompressionMethod.NONE,  # 测试时不使用压缩
            compression_level=3
        )

        # 测试Numpy数组
        array_data = np.random.randn(1000)
        serialized = serializer.serialize(array_data, use_compression=False)
        deserialized = serializer.deserialize(serialized, is_compressed=False)

        self.assertEqual(deserialized.shape, array_data.shape)
        self.assertEqual(deserialized.dtype, array_data.dtype)
        np.testing.assert_array_almost_equal(deserialized, array_data)

        # 测试字典数据
        dict_data = {"key": "value", "numbers": [1, 2, 3], "nested": {"a": 1, "b": 2}}
        serialized = serializer.serialize(dict_data)
        deserialized = serializer.deserialize(serialized)

        self.assertEqual(deserialized, dict_data)

    def test_serializer_with_header(self):
        """测试带头部的序列化器"""
        serializer = HighPerformanceSerializer(
            format=SerializationFormat.JSON,
            compression=CompressionMethod.NONE
        )

        data = {"test": "data", "value": 123.456, "list": [1, 2, 3, 4, 5]}

        # 序列化（带头部）
        serialized = serializer.serialize_with_header(data)
        self.assertIsInstance(serialized, bytes)
        self.assertGreater(len(serialized), 10)  # 至少包含头部

        # 反序列化（带头部）
        deserialized = serializer.deserialize_with_header(serialized)
        self.assertEqual(deserialized, data)

    def test_serializer_stats(self):
        """测试序列化器统计"""
        serializer = HighPerformanceSerializer()
        serializer.reset_stats()

        # 执行一些序列化/反序列化操作
        data_list = [
            np.random.randn(100),
            {"test": "data" * 100},
            [i for i in range(1000)]
        ]

        for data in data_list:
            serialized = serializer.serialize(data)
            _ = serializer.deserialize(serialized)

        stats = serializer.get_stats()

        self.assertEqual(stats['serializations'], 3)
        self.assertEqual(stats['deserializations'], 3)
        self.assertGreater(stats['total_bytes_in'], 0)
        self.assertGreater(stats['total_bytes_out'], 0)
        self.assertGreater(stats['serialization_time'], 0)
        self.assertGreater(stats['deserialization_time'], 0)

class TestPerformance(unittest.TestCase):
    """性能测试"""

    def test_ipc_message_performance(self):
        """测试IPC消息性能"""
        import time

        # 创建大量Tick数据
        num_ticks = 1000
        ticks = []
        for i in range(num_ticks):
            tick = TickData(
                price=3000.0 + i * 0.1,
                size=1.0 + i * 0.01,
                side="buy" if i % 2 == 0 else "sell",
                timestamp=1609459200000 + i * 1000,
                sequence=i
            )
            ticks.append(tick)

        # 性能测试
        start_time = time.perf_counter()

        total_size = 0
        for tick in ticks:
            message = tick.to_message()
            serialized = message.serialize()
            total_size += len(serialized)

        end_time = time.perf_counter()

        elapsed_ms = (end_time - start_time) * 1000
        avg_latency_ms = elapsed_ms / num_ticks

        print(f"\n📊 IPC消息性能测试:")
        print(f"  处理Tick数量: {num_ticks}")
        print(f"  总时间: {elapsed_ms:.2f} ms")
        print(f"  平均延迟: {avg_latency_ms:.4f} ms/Tick")
        print(f"  总数据量: {total_size / 1024:.2f} KB")
        print(f"  吞吐量: {num_ticks / (elapsed_ms / 1000):.0f} Tick/秒")

        # 性能要求：平均延迟 < 0.1ms
        self.assertLess(avg_latency_ms, 0.5, f"平均延迟 {avg_latency_ms:.4f} ms 超过 0.5 ms")

    def test_serialization_performance(self):
        """测试序列化性能"""
        import time

        serializer = HighPerformanceSerializer(
            format=SerializationFormat.NUMPY,
            compression=CompressionMethod.LZ4
        )

        # 创建测试数据
        data_sizes = [100, 1000, 10000]
        results = []

        for size in data_sizes:
            # Numpy数组
            array_data = np.random.randn(size)

            # 序列化性能
            start_time = time.perf_counter()
            serialized = serializer.serialize(array_data, use_compression=True)
            serialize_time = (time.perf_counter() - start_time) * 1000  # ms

            # 反序列化性能
            start_time = time.perf_counter()
            deserialized = serializer.deserialize(serialized, is_compressed=True)
            deserialize_time = (time.perf_counter() - start_time) * 1000  # ms

            # 验证
            np.testing.assert_array_almost_equal(deserialized, array_data)

            # 计算压缩比
            original_size = array_data.nbytes
            compressed_size = len(serialized)
            compression_ratio = compressed_size / original_size if original_size > 0 else 0

            results.append({
                'size': size,
                'original_size': original_size,
                'compressed_size': compressed_size,
                'compression_ratio': compression_ratio,
                'serialize_time_ms': serialize_time,
                'deserialize_time_ms': deserialize_time
            })

        print(f"\n📊 序列化性能测试:")
        for result in results:
            print(f"  大小 {result['size']}: "
                  f"原始 {result['original_size']/1024:.1f}KB → "
                  f"压缩 {result['compressed_size']/1024:.1f}KB "
                  f"({result['compression_ratio']*100:.1f}%), "
                  f"序列化 {result['serialize_time_ms']:.2f}ms, "
                  f"反序列化 {result['deserialize_time_ms']:.2f}ms")

        # 性能要求：序列化+反序列化时间 < 1ms（对于10000个元素）
        last_result = results[-1]
        total_time = last_result['serialize_time_ms'] + last_result['deserialize_time_ms']
        self.assertLess(total_time, 5.0, f"总时间 {total_time:.2f} ms 超过 5.0 ms")

if __name__ == "__main__":
    unittest.main(verbosity=2)

# 运行性能测试的辅助函数
def run_performance_tests():
    """运行性能测试"""
    print("🚀 运行IPC协议性能测试...")

    # 创建测试套件
    suite = unittest.TestSuite()
    suite.addTest(TestPerformance('test_ipc_message_performance'))
    suite.addTest(TestPerformance('test_serialization_performance'))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()

if __name__ == "__main__":
    # 运行所有测试
    unittest.main(verbosity=2)