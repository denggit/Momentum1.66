"""
四号引擎v3.0 JIT编译监控器测试
测试编译性能监控、告警和统计功能
"""

import unittest
import sys
import os
import time
import logging
import numpy as np

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.strategy.triplea.jit_monitor import (
    JITMonitor, CompilePhase, PerformanceLevel, AlertThreshold,
    CompileMetrics, PerformanceStats, track_function,
    get_default_monitor, get_performance_summary, analyze_function_trend,
    MonitorContext
)


class TestJITMonitor(unittest.TestCase):
    """测试JIT监控器"""

    def setUp(self):
        """设置测试环境"""
        # 设置测试日志
        logging.basicConfig(level=logging.WARNING)
        self.logger = logging.getLogger(__name__)

    def test_monitor_initialization(self):
        """测试监控器初始化"""
        # 测试不同配置
        test_cases = [
            {
                'enable_phase_tracking': False,
                'alert_threshold': AlertThreshold(
                    compile_time_ms=200.0,
                    memory_mb=50.0,
                    error_rate=0.1
                ),
                'max_history_size': 500
            },
            {
                'enable_phase_tracking': True,
                'alert_threshold': AlertThreshold(
                    compile_time_ms=500.0,
                    memory_mb=100.0,
                    error_rate=0.2
                ),
                'max_history_size': 1000
            }
        ]

        for config in test_cases:
            monitor = JITMonitor(**config)
            self.assertEqual(monitor.enable_phase_tracking, config['enable_phase_tracking'])
            self.assertEqual(monitor.max_history_size, config['max_history_size'])
            self.assertEqual(
                monitor.alert_threshold.compile_time_ms,
                config['alert_threshold'].compile_time_ms
            )

    def test_start_stop_monitoring(self):
        """测试启动和停止监控"""
        monitor = JITMonitor()

        # 初始状态
        self.assertFalse(monitor._is_monitoring)
        self.assertFalse(monitor._is_shutdown)

        # 启动监控
        success = monitor.start_monitoring()
        self.assertTrue(success)
        self.assertTrue(monitor._is_monitoring)

        # 再次启动应该失败
        success2 = monitor.start_monitoring()
        self.assertFalse(success2)

        # 停止监控
        success3 = monitor.stop_monitoring()
        self.assertTrue(success3)
        self.assertFalse(monitor._is_monitoring)

        # 关闭监控器
        monitor.shutdown()
        self.assertTrue(monitor._is_shutdown)

        # 关闭后无法启动
        success4 = monitor.start_monitoring()
        self.assertFalse(success4)

    def test_record_compile_event(self):
        """测试记录编译事件"""
        monitor = JITMonitor()
        monitor.start_monitoring()

        # 记录成功编译事件
        event_id = monitor.record_compile_event(
            function_name="test_function",
            compile_time=0.125,  # 125ms
            cache_hit=False,
            cache_source=None,
            memory_usage_bytes=1024 * 1024,  # 1MB
            success=True
        )

        self.assertIsInstance(event_id, str)
        self.assertTrue(event_id.startswith("test_function_"))

        # 记录失败编译事件
        event_id2 = monitor.record_compile_event(
            function_name="failing_function",
            compile_time=0.050,  # 50ms
            cache_hit=True,
            cache_source="disk",
            success=False,
            error_message="Compilation failed"
        )

        # 检查历史记录
        self.assertEqual(len(monitor._compile_history), 2)

        # 检查第一个事件
        first_event = monitor._compile_history[0]
        self.assertEqual(first_event.function_name, "test_function")
        self.assertEqual(first_event.compile_time, 0.125)
        self.assertFalse(first_event.cache_hit)
        self.assertTrue(first_event.success)
        self.assertIsNone(first_event.error_message)

        # 检查第二个事件
        second_event = monitor._compile_history[1]
        self.assertEqual(second_event.function_name, "failing_function")
        self.assertFalse(second_event.success)
        self.assertEqual(second_event.error_message, "Compilation failed")

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()

    def test_phase_tracking(self):
        """测试编译阶段跟踪"""
        monitor = JITMonitor(enable_phase_tracking=True)
        monitor.start_monitoring()

        # 记录带阶段时间的编译事件
        phase_times = {
            CompilePhase.TYPE_INFERENCE: 0.025,
            CompilePhase.IR_GENERATION: 0.035,
            CompilePhase.OPTIMIZATION: 0.045,
            CompilePhase.CODE_GENERATION: 0.020,
            CompilePhase.TOTAL: 0.125
        }

        monitor.record_compile_event(
            function_name="phase_tracked_function",
            compile_time=0.125,
            phase_times=phase_times,
            cache_hit=False
        )

        # 检查阶段时间被记录
        self.assertEqual(len(monitor._compile_history), 1)
        event = monitor._compile_history[0]
        self.assertEqual(len(event.phase_times), 5)
        self.assertEqual(event.phase_times[CompilePhase.TYPE_INFERENCE], 0.025)

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()

    def test_function_stats_update(self):
        """测试函数统计更新"""
        monitor = JITMonitor()
        monitor.start_monitoring()

        # 记录多次编译事件
        compile_times = [0.1, 0.2, 0.3, 0.15, 0.25]  # 单位：秒
        cache_hits = [False, True, False, True, False]

        for i, (compile_time, cache_hit) in enumerate(zip(compile_times, cache_hits)):
            monitor.record_compile_event(
                function_name="stats_test_function",
                compile_time=compile_time,
                cache_hit=cache_hit
            )

        # 获取统计信息
        stats = monitor.get_performance_stats("stats_test_function")
        self.assertIsNotNone(stats)

        # 检查统计信息
        self.assertEqual(stats.function_name, "stats_test_function")
        self.assertEqual(stats.total_compilations, 5)
        self.assertEqual(stats.cache_hits, 2)  # 两次命中
        self.assertEqual(stats.cache_misses, 3)  # 三次未命中
        self.assertAlmostEqual(stats.total_compile_time, sum(compile_times))
        self.assertAlmostEqual(stats.avg_compile_time, sum(compile_times) / 5)
        self.assertAlmostEqual(stats.min_compile_time, min(compile_times))
        self.assertAlmostEqual(stats.max_compile_time, max(compile_times))

        # 检查性能级别
        avg_ms = stats.avg_compile_time * 1000
        if avg_ms < 10:
            expected_level = PerformanceLevel.EXCELLENT
        elif avg_ms < 50:
            expected_level = PerformanceLevel.GOOD
        elif avg_ms < 200:
            expected_level = PerformanceLevel.ACCEPTABLE
        elif avg_ms < 500:
            expected_level = PerformanceLevel.SLOW
        else:
            expected_level = PerformanceLevel.CRITICAL

        self.assertEqual(stats.performance_level, expected_level)

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()

    def test_alert_triggering(self):
        """测试告警触发"""
        # 设置较低的阈值以便触发告警
        alert_threshold = AlertThreshold(
            compile_time_ms=100.0,  # 100ms阈值
            memory_mb=10.0,  # 10MB阈值
            error_rate=0.3,  # 30%错误率
            consecutive_failures=2
        )

        monitor = JITMonitor(alert_threshold=alert_threshold)
        monitor.start_monitoring()

        # 收集告警
        alerts_received = []
        def alert_callback(alert):
            alerts_received.append(alert)

        monitor.register_alert_callback(alert_callback)

        # 触发编译时间告警（125ms > 100ms阈值）
        monitor.record_compile_event(
            function_name="slow_function",
            compile_time=0.125,  # 125ms
            cache_hit=False
        )

        # 触发内存使用告警
        monitor.record_compile_event(
            function_name="memory_hungry_function",
            compile_time=0.050,
            memory_usage_bytes=15 * 1024 * 1024,  # 15MB > 10MB阈值
            cache_hit=False
        )

        # 触发错误率告警（连续错误）
        for i in range(3):
            monitor.record_compile_event(
                function_name="error_prone_function",
                compile_time=0.010,
                success=False,
                error_message=f"Error {i+1}"
            )

        # 检查告警
        self.assertGreater(len(alerts_received), 0)

        # 检查告警类型
        alert_types = [alert['type'] for alert in alerts_received]
        self.assertIn('compile_time_exceeded', alert_types)
        self.assertIn('memory_usage_exceeded', alert_types)
        self.assertIn('high_error_rate', alert_types)

        # 检查编译时间告警详情
        compile_alerts = [a for a in alerts_received if a['type'] == 'compile_time_exceeded']
        self.assertEqual(len(compile_alerts), 1)
        self.assertEqual(compile_alerts[0]['function_name'], 'slow_function')
        self.assertGreater(compile_alerts[0]['value'], 100.0)  # > 100ms

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()

    def test_performance_report(self):
        """测试性能报告生成"""
        monitor = JITMonitor()
        monitor.start_monitoring()

        # 记录多个函数的编译事件
        functions = ['func_a', 'func_b', 'func_c', 'func_d']
        for i, func_name in enumerate(functions):
            compile_time = 0.05 + i * 0.02  # 递增的编译时间
            cache_hit = (i % 2 == 0)  # 交替缓存命中

            monitor.record_compile_event(
                function_name=func_name,
                compile_time=compile_time,
                cache_hit=cache_hit
            )

        # 生成性能报告
        report = monitor.get_performance_report()

        # 检查报告结构
        self.assertIn('timestamp', report)
        self.assertIn('monitoring_active', report)
        self.assertIn('total_functions_monitored', report)
        self.assertIn('total_compilations', report)
        self.assertIn('cache_hit_rate', report)
        self.assertIn('function_performance', report)
        self.assertIn('performance_summary', report)

        # 检查具体值
        self.assertEqual(report['total_functions_monitored'], len(functions))
        self.assertEqual(report['total_compilations'], len(functions))

        # 检查函数性能排名
        self.assertEqual(len(report['function_performance']), len(functions))

        # 检查按平均编译时间排序
        perf_times = [f['avg_compile_time_ms'] for f in report['function_performance']]
        self.assertEqual(perf_times, sorted(perf_times))

        # 检查性能摘要
        summary = report['performance_summary']
        self.assertIn('excellent', summary)
        self.assertIn('good', summary)
        self.assertIn('acceptable', summary)
        self.assertIn('slow', summary)
        self.assertIn('critical', summary)

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()

    def test_trend_analysis(self):
        """测试趋势分析"""
        monitor = JITMonitor()
        monitor.start_monitoring()

        # 创建模拟趋势数据（编译时间逐渐增加）
        base_time = time.time()
        compile_times = [0.1, 0.12, 0.15, 0.19, 0.24, 0.3]  # 递增趋势

        for i, compile_time in enumerate(compile_times):
            # 模拟时间递增
            event_time = base_time + i * 3600  # 每小时一次

            # 创建事件（这里简化，实际需要修改record_compile_event以接受时间戳）
            monitor.record_compile_event(
                function_name="trend_function",
                compile_time=compile_time,
                cache_hit=False
            )

        # 获取趋势分析
        analysis = monitor.get_trend_analysis("trend_function", window_size=10)

        # 检查分析结果
        self.assertIn('function_name', analysis)
        self.assertIn('sample_count', analysis)
        self.assertIn('avg_compile_time_ms', analysis)
        self.assertIn('trend', analysis)
        self.assertIn('trend_strength', analysis)
        self.assertIn('recommendation', analysis)

        # 由于编译时间递增，趋势应该是degrading
        self.assertEqual(analysis['function_name'], 'trend_function')
        self.assertEqual(analysis['sample_count'], len(compile_times))
        self.assertEqual(analysis['trend'], 'degrading')

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()

    def test_clear_history(self):
        """测试清除历史记录"""
        monitor = JITMonitor()
        monitor.start_monitoring()

        # 记录一些事件
        for i in range(10):
            monitor.record_compile_event(
                function_name=f"func_{i}",
                compile_time=0.1,
                cache_hit=(i % 2 == 0)
            )

        # 检查历史记录
        initial_report = monitor.get_performance_report()
        self.assertEqual(initial_report['total_compilations'], 10)

        # 清除历史
        monitor.clear_history()

        # 检查是否已清除
        cleared_report = monitor.get_performance_report()
        self.assertEqual(cleared_report['total_compilations'], 0)
        self.assertEqual(cleared_report['total_functions_monitored'], 0)

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()


class TestTrackFunctionDecorator(unittest.TestCase):
    """测试函数跟踪装饰器"""

    def test_track_function_decorator(self):
        """测试track_function装饰器"""
        monitor = JITMonitor()
        monitor.start_monitoring()

        # 使用装饰器跟踪函数
        @track_function(monitor=monitor, critical=True)
        def tracked_function(x):
            time.sleep(0.001)  # 模拟工作负载
            return x * 2

        # 调用函数
        result = tracked_function(5)
        self.assertEqual(result, 10)

        # 检查是否记录了编译事件
        stats = monitor.get_performance_stats("tracked_function")
        self.assertIsNotNone(stats)
        self.assertEqual(stats.total_compilations, 1)

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()

    def test_track_function_with_error(self):
        """测试跟踪函数异常处理"""
        monitor = JITMonitor()
        monitor.start_monitoring()

        # 使用装饰器跟踪会抛出异常的函数
        @track_function(monitor=monitor)
        def error_function(x):
            if x < 0:
                raise ValueError("Negative input")
            return x * 2

        # 正常调用
        result = error_function(5)
        self.assertEqual(result, 10)

        # 异常调用
        with self.assertRaises(ValueError):
            error_function(-5)

        # 检查统计信息
        stats = monitor.get_performance_stats("error_function")
        self.assertIsNotNone(stats)
        self.assertEqual(stats.total_compilations, 2)  # 两次调用

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()


class TestConvenienceFunctions(unittest.TestCase):
    """测试便捷函数"""

    def test_get_default_monitor(self):
        """测试获取默认监控器"""
        monitor1 = get_default_monitor()
        monitor2 = get_default_monitor()

        # 应该是同一个实例
        self.assertIs(monitor1, monitor2)

        # 检查是否在监控中
        self.assertTrue(monitor1._is_monitoring)

    def test_get_performance_summary(self):
        """测试获取性能摘要"""
        summary = get_performance_summary()

        # 检查返回结构
        self.assertIsInstance(summary, dict)
        self.assertIn('timestamp', summary)
        self.assertIn('monitoring_active', summary)
        self.assertIn('total_compilations', summary)

    def test_analyze_function_trend(self):
        """测试分析函数趋势"""
        # 使用默认监控器记录一些数据
        monitor = get_default_monitor()

        # 记录测试数据
        for i in range(5):
            monitor.record_compile_event(
                function_name="test_trend_func",
                compile_time=0.1 + i * 0.01,  # 轻微递增
                cache_hit=(i % 2 == 0)
            )

        # 分析趋势
        analysis = analyze_function_trend("test_trend_func", window_size=10)

        # 检查分析结果
        self.assertIsInstance(analysis, dict)
        self.assertIn('function_name', analysis)
        self.assertIn('trend', analysis)


class TestMonitorContext(unittest.TestCase):
    """测试监控器上下文管理器"""

    def test_context_manager(self):
        """测试上下文管理器"""
        with MonitorContext(
            enable_phase_tracking=True,
            alert_threshold=AlertThreshold(compile_time_ms=100.0)
        ) as monitor:

            # 检查监控器状态
            self.assertIsInstance(monitor, JITMonitor)
            self.assertTrue(monitor._is_monitoring)
            self.assertTrue(monitor.enable_phase_tracking)

            # 在上下文中记录事件
            monitor.record_compile_event(
                function_name="context_function",
                compile_time=0.075,
                cache_hit=False
            )

        # 退出上下文后，监控器应该已关闭
        self.assertTrue(monitor._is_shutdown)

    def test_context_manager_exception(self):
        """测试上下文管理器异常处理"""
        try:
            with MonitorContext() as monitor:
                # 检查监控器状态
                self.assertTrue(monitor._is_monitoring)

                # 抛出异常
                raise ValueError("测试异常")

        except ValueError:
            # 异常应该被传播
            pass

        # 即使有异常，监控器也应该被关闭
        self.assertTrue(monitor._is_shutdown)


class TestPerformance(unittest.TestCase):
    """性能测试"""

    def test_high_frequency_event_recording(self):
        """测试高频事件记录性能"""
        print("\n🚀 高频事件记录性能测试")

        monitor = JITMonitor(max_history_size=10000)
        monitor.start_monitoring()

        num_events = 1000
        start_time = time.perf_counter()

        # 记录大量事件
        for i in range(num_events):
            monitor.record_compile_event(
                function_name=f"func_{i % 10}",  # 10个不同的函数
                compile_time=0.01 + (i % 100) * 0.0001,  # 微小变化
                cache_hit=(i % 3 == 0)
            )

        elapsed = time.perf_counter() - start_time

        print(f"  事件数量: {num_events}")
        print(f"  记录时间: {elapsed*1000:.1f}ms")
        print(f"  平均每事件: {elapsed/num_events*1000:.3f}ms")
        print(f"  事件率: {num_events/elapsed:.0f} 事件/秒")

        # 检查统计
        report = monitor.get_performance_report()
        print(f"  监控函数数: {report['total_functions_monitored']}")
        print(f"  总编译次数: {report['total_compilations']}")

        # 性能要求：每秒至少1000个事件
        events_per_second = num_events / elapsed
        self.assertGreater(events_per_second, 1000,
                          f"事件记录速率过低: {events_per_second:.0f} 事件/秒")

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()

    def test_memory_usage_with_large_history(self):
        """测试大历史记录时的内存使用"""
        print("\n💾 大历史记录内存使用测试")

        import psutil
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        monitor = JITMonitor(max_history_size=5000)
        monitor.start_monitoring()

        # 记录大量事件
        num_events = 2000
        for i in range(num_events):
            monitor.record_compile_event(
                function_name="memory_test_func",
                compile_time=0.02,
                cache_hit=False,
                memory_usage_bytes=1024 * 1024  # 1MB
            )

        # 测量内存使用
        current_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = current_memory - initial_memory

        print(f"  事件数量: {num_events}")
        print(f"  历史记录大小: {len(monitor._compile_history)}")
        print(f"  初始内存: {initial_memory:.1f} MB")
        print(f"  当前内存: {current_memory:.1f} MB")
        print(f"  内存增加: {memory_increase:.1f} MB")

        # 内存要求：增加不超过50MB
        self.assertLess(memory_increase, 50.0,
                       f"内存使用过高: {memory_increase:.1f} MB")

        # 停止监控
        monitor.stop_monitoring()
        monitor.shutdown()


def run_performance_tests():
    """运行性能测试"""
    print("\n🚀 运行JIT监控器性能测试...")

    # 创建测试套件
    suite = unittest.TestSuite()
    suite.addTest(TestPerformance('test_high_frequency_event_recording'))
    suite.addTest(TestPerformance('test_memory_usage_with_large_history'))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    print("🔬 JIT编译监控器测试")
    print("=" * 50)

    # 运行所有测试
    unittest.main(verbosity=2)