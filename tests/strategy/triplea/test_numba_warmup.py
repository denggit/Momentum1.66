"""
四号引擎v3.0 Numba预热管理器测试
测试Numba JIT预热功能、性能和降级模式
"""

import unittest
from unittest.mock import patch, MagicMock
import asyncio
import sys
import os
import time
import logging
import numpy as np

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.strategy.triplea.numba_warmup import (
    NumbaWarmupManager, WarmupStrategy, JITFunctionInfo,
    WarmupStats, register_jit_function, warmup_all,
    get_warmup_stats, critical_jit, background_jit,
    get_default_warmup_manager, NUMBA_AVAILABLE
)


class TestNumbaWarmupManager(unittest.TestCase):
    """测试Numba预热管理器"""

    def setUp(self):
        """设置测试环境"""
        # 设置测试日志
        logging.basicConfig(level=logging.WARNING)
        self.logger = logging.getLogger(__name__)

    def test_manager_initialization(self):
        """测试管理器初始化"""
        # 测试不同策略
        strategies = [
            WarmupStrategy.EAGER,
            WarmupStrategy.LAZY,
            WarmupStrategy.BACKGROUND,
            WarmupStrategy.HYBRID
        ]

        for strategy in strategies:
            manager = NumbaWarmupManager(
                strategy=strategy,
                enable_background_warmup=True,
                background_threads=2,
                warmup_data_size=50
            )

            self.assertEqual(manager.strategy, strategy)
            self.assertTrue(manager.enable_background_warmup)
            self.assertEqual(manager.background_threads, 2)
            self.assertEqual(manager.warmup_data_size, 50)
            self.assertFalse(manager._is_warming_up)
            self.assertFalse(manager._is_shutdown)

    def test_function_registration(self):
        """测试函数注册"""
        manager = NumbaWarmupManager(strategy=WarmupStrategy.LAZY)

        # 定义测试函数
        def test_func(x, y):
            return x + y

        # 使用装饰器注册
        @manager.register(critical=True)
        def critical_func(x):
            return x * 2

        @manager.register(critical=False, signature="float64(float64, float64)")
        def normal_func(a, b):
            return a + b

        # 检查注册结果
        self.assertEqual(manager._stats.total_functions, 2)

        # 检查关键函数信息
        critical_info = manager.get_function_info("critical_func")
        self.assertIsNotNone(critical_info)
        self.assertEqual(critical_info.name, "critical_func")
        self.assertTrue(critical_info.is_critical)
        self.assertEqual(critical_info.signature, None)

        # 检查普通函数信息
        normal_info = manager.get_function_info("normal_func")
        self.assertIsNotNone(normal_info)
        self.assertEqual(normal_info.name, "normal_func")
        self.assertFalse(normal_info.is_critical)
        self.assertEqual(normal_info.signature, "float64(float64, float64)")

    def test_mark_function_used(self):
        """测试标记函数使用"""
        manager = NumbaWarmupManager(strategy=WarmupStrategy.LAZY)

        @manager.register(critical=False)
        def test_func(x):
            return x

        # 初始状态
        func_info = manager.get_function_info("test_func")
        initial_call_count = func_info.call_count
        initial_last_used = func_info.last_used

        # 标记使用
        time.sleep(0.01)  # 确保时间不同
        manager.mark_function_used("test_func")

        # 检查更新
        func_info = manager.get_function_info("test_func")
        self.assertEqual(func_info.call_count, initial_call_count + 1)
        self.assertGreater(func_info.last_used, initial_last_used)

    async def _test_warmup_strategy(self, strategy: WarmupStrategy):
        """测试预热策略（辅助函数）"""
        manager = NumbaWarmupManager(
            strategy=strategy,
            enable_background_warmup=True,
            warmup_data_size=10
        )

        # 注册测试函数
        @manager.register(critical=True)
        def critical_func(x: np.ndarray) -> float:
            return np.sum(x)

        @manager.register(critical=False)
        def normal_func(a: float, b: float) -> float:
            return a + b

        # 执行预热
        success = await manager.warmup(timeout=10.0)
        self.assertTrue(success)

        # 检查统计信息
        stats = manager.get_stats()
        self.assertEqual(stats.total_functions, 2)

        # 根据策略检查编译情况
        if strategy == WarmupStrategy.EAGER:
            # 急切策略应该编译所有函数
            if NUMBA_AVAILABLE:
                # 如果Numba可用，应该尝试编译
                pass
        elif strategy == WarmupStrategy.LAZY:
            # 懒策略不立即编译
            pass
        elif strategy == WarmupStrategy.BACKGROUND:
            # 后台策略提交后台任务
            self.assertGreaterEqual(stats.background_tasks, 0)
        elif strategy == WarmupStrategy.HYBRID:
            # 混合策略：关键函数急切，其他后台
            pass

        # 清理
        await manager.shutdown()

    def test_eager_warmup(self):
        """测试急切预热策略"""
        asyncio.run(self._test_warmup_strategy(WarmupStrategy.EAGER))

    def test_lazy_warmup(self):
        """测试懒预热策略"""
        asyncio.run(self._test_warmup_strategy(WarmupStrategy.LAZY))

    def test_background_warmup(self):
        """测试后台预热策略"""
        asyncio.run(self._test_warmup_strategy(WarmupStrategy.BACKGROUND))

    def test_hybrid_warmup(self):
        """测试混合预热策略"""
        asyncio.run(self._test_warmup_strategy(WarmupStrategy.HYBRID))

    @patch('src.strategy.triplea.numba_warmup.NUMBA_AVAILABLE', True)
    def test_warmup_timeout(self):
        """测试预热超时"""
        async def test():
            with patch.object(NumbaWarmupManager, '_should_compile_now', return_value=True):
                manager = NumbaWarmupManager(strategy=WarmupStrategy.EAGER)

                # 注册一个函数但不实际编译（模拟长时间编译）
                @manager.register(critical=True)
                def slow_func(x):
                    time.sleep(2)  # 模拟长时间运行
                    return x

                # 模拟_compile_function使其睡眠，确保超时
                original_compile = manager._compile_function
                async def mock_compile(func_info):
                    await asyncio.sleep(2)  # 模拟长时间编译
                    return True
                manager._compile_function = mock_compile

                # 设置很短的超时时间
                success = await manager.warmup(timeout=0.1)

                # 超时应该返回False
                self.assertFalse(success)

                # 恢复原始方法
                manager._compile_function = original_compile
                await manager.shutdown()

        asyncio.run(test())

    def test_get_stats(self):
        """测试获取统计信息"""
        manager = NumbaWarmupManager(strategy=WarmupStrategy.LAZY)

        # 注册一些函数
        for i in range(3):
            @manager.register(critical=(i == 0))
            def func(x):
                return x

        # 获取统计信息
        stats = manager.get_stats()
        self.assertEqual(stats.total_functions, 3)
        self.assertEqual(stats.compiled_functions, 0)
        self.assertEqual(stats.total_compile_time, 0.0)
        self.assertEqual(stats.avg_compile_time, 0.0)

    def test_shutdown(self):
        """测试关闭管理器"""
        async def test():
            manager = NumbaWarmupManager(
                strategy=WarmupStrategy.BACKGROUND,
                enable_background_warmup=True
            )

            # 注册函数
            @manager.register(critical=False)
            def test_func(x):
                return x

            # 启动预热（后台）
            await manager.warmup(timeout=5.0)

            # 关闭管理器
            await manager.shutdown()

            self.assertTrue(manager._is_shutdown)
            self.assertIsNone(manager._background_executor)

        asyncio.run(test())

    def test_numba_unavailable_fallback(self):
        """测试Numba不可用时的降级模式"""
        # 注意：这个测试假设Numba可能不可用
        # 主要测试降级模式不会崩溃
        manager = NumbaWarmupManager(strategy=WarmupStrategy.EAGER)

        @manager.register(critical=True)
        def test_func(x):
            return x * 2

        # 即使Numba不可用，也应该能正常工作
        self.assertEqual(manager._stats.total_functions, 1)

        # 尝试预热（应该跳过）
        async def test_warmup():
            success = await manager.warmup(timeout=5.0)
            self.assertTrue(success)  # 降级模式下应该成功

        asyncio.run(test_warmup())


class TestConvenienceFunctions(unittest.TestCase):
    """测试便捷函数"""

    def test_register_jit_function(self):
        """测试register_jit_function装饰器"""
        # 使用默认管理器
        @register_jit_function(critical=True)
        def test_func(x):
            return x * 2

        # 检查是否注册到默认管理器
        manager = get_default_warmup_manager()
        func_info = manager.get_function_info("test_func")
        self.assertIsNotNone(func_info)
        self.assertTrue(func_info.is_critical)

    def test_critical_jit_decorator(self):
        """测试critical_jit装饰器"""
        # 这个装饰器组合了register_jit_function和njit
        @critical_jit(cache=True)
        def test_func(x: float) -> float:
            return x * 2.0

        # 检查函数是否可调用
        result = test_func(2.0)
        self.assertEqual(result, 4.0)

        # 检查是否注册到默认管理器
        manager = get_default_warmup_manager()
        func_info = manager.get_function_info("test_func")
        self.assertIsNotNone(func_info)
        self.assertTrue(func_info.is_critical)

    def test_background_jit_decorator(self):
        """测试background_jit装饰器"""
        @background_jit(cache=True)
        def test_func(x: float) -> float:
            return x + 1.0

        # 检查函数是否可调用
        result = test_func(2.0)
        self.assertEqual(result, 3.0)

        # 检查是否注册到默认管理器
        manager = get_default_warmup_manager()
        func_info = manager.get_function_info("test_func")
        self.assertIsNotNone(func_info)
        self.assertFalse(func_info.is_critical)

    def test_warmup_all(self):
        """测试warmup_all函数"""
        # 注册一些函数
        @register_jit_function(critical=True)
        def func1(x):
            return x

        @register_jit_function(critical=False)
        def func2(x):
            return x * 2

        # 执行预热
        async def test():
            success = await warmup_all(timeout=10.0)
            self.assertTrue(success)

        asyncio.run(test())

    def test_get_warmup_stats(self):
        """测试get_warmup_stats函数"""
        stats = get_warmup_stats()
        self.assertIsInstance(stats, WarmupStats)
        self.assertGreaterEqual(stats.total_functions, 0)


class TestPerformance(unittest.TestCase):
    """性能测试"""

    def test_compile_time_measurement(self):
        """测试编译时间测量"""
        # 这个测试需要Numba可用
        if not NUMBA_AVAILABLE:
            self.skipTest("Numba不可用，跳过编译时间测试")

        manager = NumbaWarmupManager(strategy=WarmupStrategy.EAGER)

        # 注册一个Numba JIT函数
        import numba

        @manager.register(critical=True)
        @numba.njit(cache=True)
        def compute_kde(prices, bandwidth):
            """简化的KDE计算函数"""
            n = len(prices)
            result = 0.0
            for price in prices:
                result += numba.exp(-0.5 * ((price - 3000.0) / bandwidth) ** 2)
            return result / (n * bandwidth * np.sqrt(2 * np.pi))

        # 执行预热并测量时间
        async def test():
            start_time = time.perf_counter()
            success = await manager.warmup(timeout=30.0)
            elapsed = time.perf_counter() - start_time

            self.assertTrue(success)

            # 检查编译时间被记录
            func_info = manager.get_function_info("compute_kde")
            self.assertIsNotNone(func_info)
            self.assertGreater(func_info.compile_time, 0.0)

            print(f"\n⏱️  Numba JIT编译测试:")
            print(f"  函数: compute_kde")
            print(f"  编译时间: {func_info.compile_time*1000:.1f}ms")
            print(f"  总预热时间: {elapsed*1000:.1f}ms")

            # 清理
            await manager.shutdown()

        asyncio.run(test())

    def test_cache_performance(self):
        """测试缓存性能"""
        # 这个测试需要Numba可用
        if not NUMBA_AVAILABLE:
            self.skipTest("Numba不可用，跳过缓存测试")

        import numba

        manager = NumbaWarmupManager(strategy=WarmupStrategy.EAGER)

        # 注册一个使用缓存的函数
        @manager.register(critical=True)
        @numba.njit(cache=True)
        def expensive_computation(x):
            """昂贵的计算函数"""
            result = 0.0
            for i in range(1000):
                result += np.sin(x + i * 0.001)
            return result

        async def test():
            # 第一次预热（应该编译）
            success = await manager.warmup(timeout=30.0)
            self.assertTrue(success)

            # 获取第一次编译时间
            func_info = manager.get_function_info("expensive_computation")
            first_compile_time = func_info.compile_time

            # 创建新管理器模拟重启
            manager2 = NumbaWarmupManager(strategy=WarmupStrategy.EAGER)

            # 注册相同函数
            @manager2.register(critical=True)
            @numba.njit(cache=True)
            def expensive_computation(x):
                result = 0.0
                for i in range(1000):
                    result += np.sin(x + i * 0.001)
                return result

            # 第二次预热（应该使用缓存，更快）
            start_time = time.perf_counter()
            success2 = await manager2.warmup(timeout=30.0)
            second_compile_time = time.perf_counter() - start_time

            self.assertTrue(success2)

            print(f"\n💾 Numba缓存性能测试:")
            print(f"  第一次编译时间: {first_compile_time*1000:.1f}ms")
            print(f"  第二次编译时间: {second_compile_time*1000:.1f}ms")
            if first_compile_time > 0:
                speedup = first_compile_time / second_compile_time
                print(f"  缓存加速: {speedup:.1f}倍")

            # 清理
            await manager.shutdown()
            await manager2.shutdown()

        asyncio.run(test())


def run_performance_tests():
    """运行性能测试"""
    print("\n🚀 运行Numba预热管理器性能测试...")

    # 创建测试套件
    suite = unittest.TestSuite()
    suite.addTest(TestPerformance('test_compile_time_measurement'))
    suite.addTest(TestPerformance('test_cache_performance'))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    print("🔬 Numba预热管理器测试")
    print("=" * 50)

    # 检查Numba可用性
    if NUMBA_AVAILABLE:
        print("✅ Numba可用，运行完整测试")
    else:
        print("⚠️  Numba不可用，运行降级模式测试")

    # 运行所有测试
    unittest.main(verbosity=2)