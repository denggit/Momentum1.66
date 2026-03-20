"""
四号引擎v3.0 Numba缓存管理器测试
测试缓存管理功能、清理策略和性能
"""

import unittest
import sys
import os
import tempfile
import shutil
import time
import json
import numpy as np
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.strategy.triplea.numba_cache import (
    NumbaCacheManager, CacheCleanupStrategy, CacheStats,
    get_default_cache_manager, cleanup_cache, get_cache_stats,
    clear_all_cache, get_cache_health, CacheManagerContext
)


class TestNumbaCacheManager(unittest.TestCase):
    """测试Numba缓存管理器"""

    def setUp(self):
        """设置测试环境"""
        # 创建临时缓存目录
        self.temp_dir = tempfile.mkdtemp(prefix="test_numba_cache_")
        self.cache_dir = os.path.join(self.temp_dir, "cache")

    def tearDown(self):
        """清理测试环境"""
        # 删除临时目录
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_test_cache_files(self, num_files=5, sizes_kb=None):
        """创建测试缓存文件"""
        if sizes_kb is None:
            sizes_kb = [10, 20, 30, 40, 50]  # KB

        created_files = []
        for i in range(num_files):
            file_path = os.path.join(self.cache_dir, f"test_file_{i}.nbc")
            size_bytes = sizes_kb[i % len(sizes_kb)] * 1024

            # 创建文件
            with open(file_path, 'wb') as f:
                f.write(os.urandom(size_bytes))

            # 修改文件时间戳（模拟不同年龄的文件）
            age_days = i  # 第i个文件有i天的年龄
            access_time = time.time() - age_days * 86400
            modify_time = time.time() - age_days * 86400

            os.utime(file_path, (access_time, modify_time))
            created_files.append(file_path)

        return created_files

    def test_manager_initialization(self):
        """测试管理器初始化"""
        # 测试不同配置
        test_cases = [
            {
                'cache_dir': self.cache_dir,
                'max_size_mb': 100,
                'cleanup_strategy': CacheCleanupStrategy.AGE_BASED,
                'enable_file_locking': True
            },
            {
                'cache_dir': self.cache_dir,
                'max_size_mb': 500,
                'cleanup_strategy': CacheCleanupStrategy.SIZE_BASED,
                'enable_file_locking': False
            },
            {
                'cache_dir': self.cache_dir,
                'max_size_mb': 1000,
                'cleanup_strategy': CacheCleanupStrategy.HYBRID,
                'enable_file_locking': True
            }
        ]

        for config in test_cases:
            manager = NumbaCacheManager(**config)
            self.assertEqual(manager.cache_dir, config['cache_dir'])
            self.assertEqual(manager.max_size_bytes, config['max_size_mb'] * 1024 * 1024)
            self.assertEqual(manager.cleanup_strategy, config['cleanup_strategy'])
            self.assertEqual(manager.enable_file_locking, config['enable_file_locking'])

    def test_cache_directory_creation(self):
        """测试缓存目录创建"""
        # 确保目录不存在
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)

        manager = NumbaCacheManager(cache_dir=self.cache_dir)
        success = manager.initialize()

        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.cache_dir))
        self.assertTrue(os.path.exists(os.path.join(self.cache_dir, "cache_metadata.json")))

        # 检查管理器状态
        self.assertTrue(manager._is_initialized)
        self.assertFalse(manager._is_shutdown)

    def test_metadata_persistence(self):
        """测试元数据持久化"""
        manager = NumbaCacheManager(cache_dir=self.cache_dir)
        manager.initialize()

        # 创建一些测试文件
        self._create_test_cache_files(3)

        # 手动扫描文件
        manager._scan_cache_files()

        # 获取初始统计
        initial_stats = manager.get_stats()
        self.assertEqual(initial_stats.total_files, 4)  # 3个测试文件 + 1个元数据文件

        # 创建新管理器实例（模拟重启）
        manager2 = NumbaCacheManager(cache_dir=self.cache_dir)
        manager2.initialize()

        # 检查元数据是否正确加载
        loaded_stats = manager2.get_stats()
        self.assertEqual(loaded_stats.total_files, 4)  # 3个测试文件 + 1个元数据文件
        # 允许元数据文件大小微小差异（如时间戳变化）
        self.assertLess(abs(loaded_stats.total_size_bytes - initial_stats.total_size_bytes), 10)

        # 清理
        manager.shutdown()
        manager2.shutdown()

    def test_cache_cleanup_age_based(self):
        """测试基于年龄的缓存清理"""
        manager = NumbaCacheManager(
            cache_dir=self.cache_dir,
            cleanup_strategy=CacheCleanupStrategy.AGE_BASED
        )
        manager.initialize()

        # 创建测试文件（不同年龄）
        self._create_test_cache_files(5)

        # 初始扫描
        manager._scan_cache_files()
        initial_stats = manager.get_stats()
        # 包括元数据文件，所以是6个文件（5个测试文件 + 1个元数据文件）
        self.assertEqual(initial_stats.total_files, 6)

        # 清理超过2天的文件（文件0-2应该被删除，3-4保留）
        deleted_count, reclaimed_bytes = manager.cleanup(max_age_days=2)

        # 检查结果
        self.assertEqual(deleted_count, 3)  # 文件0,1,2（年龄0,1,2天）
        self.assertGreater(reclaimed_bytes, 0)

        # 检查更新后的统计
        updated_stats = manager.get_stats()
        # 清理后剩下3个文件（2个年轻文件 + 1个元数据文件）
        self.assertEqual(updated_stats.total_files, 3)

        # 清理
        manager.shutdown()

    def test_cache_cleanup_size_based(self):
        """测试基于大小的缓存清理"""
        # 创建小缓存限制
        manager = NumbaCacheManager(
            cache_dir=self.cache_dir,
            max_size_mb=0.1,  # 100KB限制
            cleanup_strategy=CacheCleanupStrategy.SIZE_BASED
        )
        manager.initialize()

        # 创建5个文件，每个20KB（总共100KB）
        self._create_test_cache_files(5, sizes_kb=[20, 20, 20, 20, 20])

        # 初始扫描
        manager._scan_cache_files()
        initial_stats = manager.get_stats()
        total_size_kb = initial_stats.total_size_bytes / 1024
        self.assertGreater(total_size_kb, 99)  # 约100KB

        # 清理到50KB限制
        deleted_count, reclaimed_bytes = manager.cleanup(max_size_mb=0.05)  # 50KB

        # 检查结果
        self.assertGreater(deleted_count, 0)
        self.assertGreater(reclaimed_bytes, 0)

        # 检查更新后的统计
        updated_stats = manager.get_stats()
        updated_size_kb = updated_stats.total_size_bytes / 1024
        self.assertLessEqual(updated_size_kb, 50)

        # 清理
        manager.shutdown()

    def test_cache_cleanup_dry_run(self):
        """测试模拟运行的缓存清理"""
        manager = NumbaCacheManager(cache_dir=self.cache_dir)
        manager.initialize()

        # 创建测试文件
        self._create_test_cache_files(3)

        # 初始扫描
        manager._scan_cache_files()
        initial_stats = manager.get_stats()

        # 模拟清理
        deleted_count, reclaimed_bytes = manager.cleanup(max_age_days=1, dry_run=True)

        # 检查模拟结果
        self.assertGreater(deleted_count, 0)
        self.assertGreater(reclaimed_bytes, 0)

        # 确保文件没有被实际删除
        updated_stats = manager.get_stats()
        self.assertEqual(updated_stats.total_files, initial_stats.total_files)

        # 清理
        manager.shutdown()

    def test_clear_all_cache(self):
        """测试清空所有缓存"""
        manager = NumbaCacheManager(cache_dir=self.cache_dir)
        manager.initialize()

        # 创建测试文件
        self._create_test_cache_files(5)

        # 初始扫描
        manager._scan_cache_files()
        initial_stats = manager.get_stats()
        # 包括5个测试文件 + 1个元数据文件
        self.assertEqual(initial_stats.total_files, 6)

        # 清空缓存
        deleted_count, reclaimed_bytes = manager.clear_all()

        # 检查结果
        self.assertEqual(deleted_count, 6)  # 包括5个测试文件 + 1个元数据文件
        self.assertGreater(reclaimed_bytes, 0)

        # 检查目录是否为空
        self.assertEqual(len(os.listdir(self.cache_dir)), 1)  # 只有元数据文件
        self.assertTrue(os.path.exists(os.path.join(self.cache_dir, "cache_metadata.json")))

        # 检查更新后的统计
        updated_stats = manager.get_stats()
        # 只有元数据文件被重新创建
        self.assertEqual(updated_stats.total_files, 1)
        # 元数据文件很小，但非零
        self.assertGreater(updated_stats.total_size_bytes, 0)
        self.assertLess(updated_stats.total_size_bytes, 1024)  # 小于1KB

        # 清理
        manager.shutdown()

    def test_get_cache_health(self):
        """测试获取缓存健康状态"""
        manager = NumbaCacheManager(
            cache_dir=self.cache_dir,
            max_size_mb=1  # 1MB限制
        )
        manager.initialize()

        # 创建测试文件
        self._create_test_cache_files(3, sizes_kb=[100, 200, 300])  # 总共600KB

        # 扫描文件
        manager._scan_cache_files()

        # 获取健康状态
        health = manager.get_cache_health()

        # 检查健康状态字段
        self.assertIn('total_files', health)
        self.assertIn('total_size_bytes', health)
        self.assertIn('max_size_bytes', health)
        self.assertIn('size_utilization_percent', health)
        self.assertIn('health_status', health)

        # 检查具体值
        self.assertEqual(health['total_files'], 4)  # 3个测试文件 + 1个元数据文件
        self.assertEqual(health['max_size_bytes'], 1 * 1024 * 1024)

        # 利用率应该在60%左右（600KB / 1MB）
        self.assertGreater(health['size_utilization_percent'], 50)
        self.assertLess(health['size_utilization_percent'], 70)

        # 健康状态应该是healthy或warning
        self.assertIn(health['health_status'], ['healthy', 'warning'])

        # 清理
        manager.shutdown()

    def test_file_locking(self):
        """测试文件锁定功能"""
        manager = NumbaCacheManager(
            cache_dir=self.cache_dir,
            enable_file_locking=True
        )
        manager.initialize()

        # 创建测试文件
        test_file = os.path.join(self.cache_dir, "test_file.nbc")
        with open(test_file, 'wb') as f:
            f.write(b"test data")

        # 扫描文件
        manager._scan_cache_files()

        # 锁定文件
        success = manager.lock_file(test_file)
        self.assertTrue(success)

        # 检查锁定状态
        file_info = manager.get_file_info(test_file)
        self.assertIsNotNone(file_info)
        self.assertTrue(file_info.is_locked)

        # 再次尝试锁定应该失败
        success2 = manager.lock_file(test_file)
        self.assertFalse(success2)

        # 解锁文件
        success3 = manager.unlock_file(test_file)
        self.assertTrue(success3)

        # 检查解锁状态
        file_info = manager.get_file_info(test_file)
        self.assertIsNotNone(file_info)
        self.assertFalse(file_info.is_locked)

        # 清理
        manager.shutdown()

    def test_mark_file_accessed(self):
        """测试标记文件访问"""
        manager = NumbaCacheManager(cache_dir=self.cache_dir)
        manager.initialize()

        # 创建测试文件
        test_file = os.path.join(self.cache_dir, "test_file.nbc")
        with open(test_file, 'wb') as f:
            f.write(b"test data")

        # 扫描文件
        manager._scan_cache_files()

        # 获取初始访问信息
        file_info = manager.get_file_info(test_file)
        self.assertIsNotNone(file_info)
        initial_access_count = file_info.access_count
        initial_last_accessed = file_info.last_accessed_time

        # 等待一小段时间
        time.sleep(0.01)

        # 标记文件访问
        success = manager.mark_file_accessed(test_file)
        self.assertTrue(success)

        # 检查更新后的信息
        file_info = manager.get_file_info(test_file)
        self.assertIsNotNone(file_info)
        self.assertEqual(file_info.access_count, initial_access_count + 1)
        self.assertGreater(file_info.last_accessed_time, initial_last_accessed)

        # 清理
        manager.shutdown()

    def test_shutdown(self):
        """测试关闭管理器"""
        manager = NumbaCacheManager(cache_dir=self.cache_dir)
        manager.initialize()

        # 创建测试文件
        self._create_test_cache_files(2)

        # 扫描文件
        manager._scan_cache_files()

        # 关闭管理器
        success = manager.shutdown()
        self.assertTrue(success)
        self.assertTrue(manager._is_shutdown)

        # 确保元数据文件存在
        self.assertTrue(os.path.exists(os.path.join(self.cache_dir, "cache_metadata.json")))


class TestConvenienceFunctions(unittest.TestCase):
    """测试便捷函数"""

    def setUp(self):
        """设置测试环境"""
        self.temp_dir = tempfile.mkdtemp(prefix="test_numba_cache_conv_")
        self.cache_dir = os.path.join(self.temp_dir, "cache")

    def tearDown(self):
        """清理测试环境"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_default_cache_manager(self):
        """测试获取默认缓存管理器"""
        manager1 = get_default_cache_manager()
        manager2 = get_default_cache_manager()

        # 应该是同一个实例
        self.assertIs(manager1, manager2)

        # 检查是否已初始化
        self.assertTrue(manager1._is_initialized)

    def test_cleanup_cache(self):
        """测试cleanup_cache便捷函数"""
        # 使用临时目录创建自定义管理器
        with CacheManagerContext(cache_dir=self.cache_dir, max_size_mb=1) as manager:
            # 创建测试文件
            for i in range(3):
                file_path = os.path.join(self.cache_dir, f"test_{i}.nbc")
                with open(file_path, 'wb') as f:
                    f.write(os.urandom(50 * 1024))  # 50KB

            # 扫描文件
            manager._scan_cache_files()

            # 使用便捷函数清理
            deleted_count, reclaimed_bytes = cleanup_cache(max_age_days=0, dry_run=True)

            # 检查结果
            self.assertGreater(deleted_count, 0)
            self.assertGreater(reclaimed_bytes, 0)

    def test_get_cache_stats(self):
        """测试get_cache_stats便捷函数"""
        stats = get_cache_stats()

        # 检查返回类型
        self.assertIsInstance(stats, CacheStats)

        # 检查基本字段
        self.assertIsInstance(stats.total_files, int)
        self.assertIsInstance(stats.total_size_bytes, int)
        self.assertIsInstance(stats.avg_file_size_bytes, int)

    def test_get_cache_health(self):
        """测试get_cache_health便捷函数"""
        health = get_cache_health()

        # 检查返回类型
        self.assertIsInstance(health, dict)

        # 检查关键字段
        self.assertIn('total_files', health)
        self.assertIn('total_size_bytes', health)
        self.assertIn('health_status', health)

    def test_clear_all_cache_convenience(self):
        """测试clear_all_cache便捷函数"""
        # 使用临时目录创建自定义管理器
        with CacheManagerContext(cache_dir=self.cache_dir, max_size_mb=1) as manager:
            # 创建测试文件
            for i in range(3):
                file_path = os.path.join(self.cache_dir, f"test_{i}.nbc")
                with open(file_path, 'wb') as f:
                    f.write(os.urandom(10 * 1024))

            # 扫描文件
            manager._scan_cache_files()
            initial_stats = manager.get_stats()
            self.assertGreater(initial_stats.total_files, 0)

            # 使用便捷函数清空（模拟运行）
            deleted_count, reclaimed_bytes = clear_all_cache(dry_run=True)

            # 检查结果
            self.assertGreater(deleted_count, 0)
            self.assertGreater(reclaimed_bytes, 0)

            # 文件应该没有被删除
            self.assertEqual(manager.get_stats().total_files, initial_stats.total_files)


class TestCacheManagerContext(unittest.TestCase):
    """测试缓存管理器上下文"""

    def setUp(self):
        """设置测试环境"""
        self.temp_dir = tempfile.mkdtemp(prefix="test_numba_cache_ctx_")

    def tearDown(self):
        """清理测试环境"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_context_manager(self):
        """测试上下文管理器"""
        cache_dir = os.path.join(self.temp_dir, "cache")

        with CacheManagerContext(cache_dir=cache_dir, max_size_mb=10) as manager:
            # 检查管理器状态
            self.assertIsInstance(manager, NumbaCacheManager)
            self.assertTrue(manager._is_initialized)
            self.assertFalse(manager._is_shutdown)

            # 创建测试文件
            test_file = os.path.join(cache_dir, "test.nbc")
            with open(test_file, 'wb') as f:
                f.write(b"test data")

            # 扫描文件
            manager._scan_cache_files()
            stats = manager.get_stats()
            self.assertEqual(stats.total_files, 2)  # 测试文件 + 元数据文件

        # 退出上下文后，管理器应该已关闭
        self.assertTrue(manager._is_shutdown)

    def test_context_manager_exception(self):
        """测试上下文管理器异常处理"""
        cache_dir = os.path.join(self.temp_dir, "cache")

        try:
            with CacheManagerContext(cache_dir=cache_dir, max_size_mb=10) as manager:
                # 检查管理器状态
                self.assertTrue(manager._is_initialized)

                # 抛出异常
                raise ValueError("测试异常")

        except ValueError:
            # 异常应该被传播
            pass

        # 即使有异常，管理器也应该被关闭
        self.assertTrue(manager._is_shutdown)


def run_cache_manager_tests():
    """运行缓存管理器测试"""
    print("🧪 运行Numba缓存管理器测试...")

    # 创建测试套件
    suite = unittest.TestSuite()
    suite.addTest(TestNumbaCacheManager('test_manager_initialization'))
    suite.addTest(TestNumbaCacheManager('test_cache_directory_creation'))
    suite.addTest(TestNumbaCacheManager('test_metadata_persistence'))
    suite.addTest(TestNumbaCacheManager('test_cache_cleanup_age_based'))
    suite.addTest(TestNumbaCacheManager('test_cache_cleanup_dry_run'))
    suite.addTest(TestNumbaCacheManager('test_get_cache_health'))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    print("🔬 Numba缓存管理器测试")
    print("=" * 50)

    # 运行所有测试
    unittest.main(verbosity=2)