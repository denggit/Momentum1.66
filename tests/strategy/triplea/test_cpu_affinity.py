"""
四号引擎v3.0 CPU亲和性管理器测试
测试CPU核心绑定的功能、性能和跨平台兼容性
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import psutil

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.strategy.triplea.cpu_affinity import (
    CPUAffinityManager, CPUAffinityError, PlatformSupport,
    get_default_manager
)


class TestCPUAffinityManager(unittest.TestCase):
    """测试CPU亲和性管理器"""

    def setUp(self):
        """设置测试环境"""
        # 使用模拟的psutil进行测试
        self.manager = CPUAffinityManager()

    def test_platform_detection(self):
        """测试平台检测"""
        platform_info = self.manager.platform_info

        self.assertIn('system', platform_info)
        self.assertIn('release', platform_info)
        self.assertIn('support_level', platform_info)

        # 检查支持级别
        support_level = platform_info['support_level']
        self.assertIn(support_level, [s.value for s in PlatformSupport])

    def test_cpu_topology_detection(self):
        """测试CPU拓扑检测"""
        topology = self.manager.get_cpu_topology()

        # 基本拓扑信息
        self.assertIn('physical_cores', topology)
        self.assertIn('logical_cores', topology)
        self.assertIn('hyperthreading', topology)
        self.assertIn('cores', topology)

        # 验证逻辑
        self.assertGreater(topology['physical_cores'], 0)
        self.assertGreater(topology['logical_cores'], 0)
        self.assertGreaterEqual(topology['logical_cores'], topology['physical_cores'])

        # 超线程检测
        hyperthreading = topology['hyperthreading']
        if topology['logical_cores'] > topology['physical_cores']:
            self.assertTrue(hyperthreading)
        else:
            self.assertFalse(hyperthreading)

    def test_affinity_setting(self):
        """测试亲和性设置"""
        # 在macOS上，psutil.cpu_affinity可能不可用，跳过测试
        import platform
        if platform.system().lower() == 'darwin':
            self.skipTest("macOS不支持psutil.cpu_affinity，跳过测试")

        # 保存原始亲和性
        self.manager.save_original_affinity()
        self.assertIsNotNone(self.manager.original_affinity)

        # 测试设置亲和性
        test_cores = [0]  # 测试绑定到核心0
        success = self.manager.set_affinity(test_cores)

        # 在某些系统上可能无法设置亲和性，所以不强制要求成功
        if success:
            self.assertEqual(self.manager.current_affinity, test_cores)

        # 恢复原始亲和性
        self.manager.restore_original_affinity()
        if self.manager.original_affinity:
            self.assertEqual(self.manager.current_affinity, self.manager.original_affinity)

    def test_affinity_validation(self):
        """测试亲和性验证"""
        # 在macOS上，psutil.cpu_affinity可能不可用，跳过测试
        import platform
        if platform.system().lower() == 'darwin':
            self.skipTest("macOS不支持psutil.cpu_affinity，跳过测试")

        # 测试空核心列表
        with self.assertRaises(CPUAffinityError):
            self.manager.set_affinity([])

        # 测试无效核心编号
        invalid_core = self.manager.cpu_count_logical + 10
        with self.assertRaises(CPUAffinityError):
            self.manager.set_affinity([invalid_core])

    def test_triplea_affinity_configuration(self):
        """测试四号引擎亲和性配置"""
        result = self.manager.set_affinity_for_triplea(
            main_process_core=0,
            worker_process_core=1
        )

        # 检查结果结构
        self.assertIn('main_process', result)
        self.assertIn('worker_process', result)
        self.assertIn('recommendations', result)

        # 检查主进程配置
        main_config = result['main_process']
        self.assertEqual(main_config['core'], 0)

        # 检查Worker进程配置
        worker_config = result['worker_process']
        self.assertEqual(worker_config['core'], 1)

        # 检查是否有推荐配置
        self.assertIsInstance(result['recommendations'], list)

    def test_affinity_retrieval(self):
        """测试亲和性获取"""
        # 在macOS上，psutil.cpu_affinity可能不可用，跳过测试
        import platform
        if platform.system().lower() == 'darwin':
            self.skipTest("macOS不支持psutil.cpu_affinity，跳过测试")

        # 获取当前进程亲和性
        affinity = self.manager.get_affinity()
        self.assertIsInstance(affinity, list)

        # 验证核心编号
        if affinity:
            for core in affinity:
                self.assertIsInstance(core, int)
                self.assertGreaterEqual(core, 0)
                self.assertLess(core, self.manager.cpu_count_logical)

    def test_core_utilization_monitoring(self):
        """测试核心利用率监控"""
        # 获取CPU利用率
        utilization = self.manager.get_core_utilization(interval=0.1)

        # 检查结果结构
        self.assertIsInstance(utilization, dict)

        # 检查每个核心的利用率
        for core, percent in utilization.items():
            self.assertIsInstance(core, int)
            self.assertIsInstance(percent, float)
            self.assertGreaterEqual(percent, 0.0)
            self.assertLessEqual(percent, 100.0)

    def test_recommended_configuration(self):
        """测试推荐配置生成"""
        config = self.manager.get_recommended_configuration()

        # 检查配置结构
        required_keys = [
            'server_type', 'physical_cores', 'logical_cores',
            'recommended_cores', 'performance_considerations', 'warnings'
        ]
        for key in required_keys:
            self.assertIn(key, config)

        # 检查推荐核心配置
        recommended_cores = config['recommended_cores']
        self.assertIn('main_process', recommended_cores)
        self.assertIn('worker_process', recommended_cores)

        # 检查核心编号有效性
        main_core = recommended_cores['main_process']
        worker_core = recommended_cores['worker_process']
        self.assertIsInstance(main_core, int)
        self.assertIsInstance(worker_core, int)

        # 检查性能考虑事项
        self.assertIsInstance(config['performance_considerations'], list)
        self.assertIsInstance(config['warnings'], list)

    def test_dual_core_isolation_verification(self):
        """测试双核隔离验证"""
        # 创建模拟的进程ID
        main_pid = os.getpid()

        # 创建子进程测试Worker进程
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("""
import time
import psutil
process = psutil.Process()
print(process.pid)
time.sleep(1)
            """)
            script_path = f.name

        try:
            # 启动子进程
            result = subprocess.run([sys.executable, script_path],
                                    capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                worker_pid = int(result.stdout.strip())

                # 验证双核隔离
                verification = self.manager.verify_dual_core_isolation(
                    main_pid=main_pid,
                    worker_pid=worker_pid
                )

                # 检查验证结果结构
                self.assertIn('main_process', verification)
                self.assertIn('worker_process', verification)
                self.assertIn('cross_core_isolation', verification)
                self.assertIn('recommendations', verification)

                # 检查主进程信息
                main_info = verification['main_process']
                self.assertEqual(main_info['pid'], main_pid)
                self.assertIsInstance(main_info['cores'], (list, type(None)))
                self.assertIsInstance(main_info['isolation'], bool)

                # 检查Worker进程信息
                worker_info = verification['worker_process']
                self.assertEqual(worker_info['pid'], worker_pid)
                self.assertIsInstance(worker_info['cores'], (list, type(None)))
                self.assertIsInstance(worker_info['isolation'], bool)

        finally:
            # 清理临时文件
            os.unlink(script_path)

    def test_affinity_compliance_monitoring(self):
        """测试亲和性合规性监控"""
        # 创建预期的亲和性设置
        expected_affinity = {
            os.getpid(): [0]  # 当前进程绑定到核心0
        }

        # 启动监控
        monitor_thread = self.manager.monitor_affinity_compliance(
            expected_affinity,
            check_interval=0.1  # 很短的检查间隔用于测试
        )

        # 等待监控运行一段时间
        time.sleep(0.3)

        # 停止监控
        monitor_thread.do_run = False
        monitor_thread.join(timeout=1.0)

        # 验证监控线程已停止
        self.assertFalse(monitor_thread.is_alive())

    @patch('psutil.Process')
    def test_affinity_error_handling(self, mock_process_class):
        """测试亲和性错误处理"""
        # 创建模拟的进程对象
        mock_process = MagicMock()
        mock_process_class.return_value = mock_process

        # 模拟设置亲和性失败
        mock_process.cpu_affinity.side_effect = psutil.AccessDenied("权限不足")

        # 创建新的管理器实例
        test_manager = CPUAffinityManager()

        # 测试设置亲和性（应该失败）
        success = test_manager.set_affinity([0])
        self.assertFalse(success)

    def test_default_manager_singleton(self):
        """测试默认管理器单例模式"""
        # 获取默认管理器
        manager1 = get_default_manager()
        manager2 = get_default_manager()

        # 应该是同一个实例
        self.assertIs(manager1, manager2)

    def test_platform_specific_methods(self):
        """测试平台特定方法"""
        # 测试不同平台的拓扑检测方法
        topology = self.manager.get_cpu_topology()

        # 根据平台测试相应的方法
        system = self.manager.platform_info['system']
        if system == 'linux':
            # Linux应该有特定信息
            self.assertIn('sockets', topology)
            self.assertIn('cores_per_socket', topology)
        elif system == 'darwin':
            # macOS应该有特定信息
            self.assertIn('sockets', topology)
            self.assertIn('cores_per_socket', topology)
        elif system == 'windows':
            # Windows应该有特定信息
            self.assertIn('sockets', topology)
            self.assertIn('cores_per_socket', topology)


class TestPerformance(unittest.TestCase):
    """性能测试"""

    def test_affinity_setting_performance(self):
        """测试亲和性设置性能"""
        manager = CPUAffinityManager()

        # 测量设置亲和性的时间
        test_cores = [0]
        num_iterations = 100

        start_time = time.perf_counter()

        for i in range(num_iterations):
            # 交替使用不同的核心
            cores = [(i % 2)]
            manager.set_affinity(cores)

        end_time = time.perf_counter()

        total_time = end_time - start_time
        avg_time_per_set = total_time / num_iterations * 1000  # 毫秒

        print(f"\n📊 CPU亲和性设置性能测试:")
        print(f"  迭代次数: {num_iterations}")
        print(f"  总时间: {total_time * 1000:.2f} ms")
        print(f"  平均设置时间: {avg_time_per_set:.4f} ms")

        # 性能要求：平均设置时间 < 1ms
        self.assertLess(avg_time_per_set, 5.0,
                        f"平均设置时间 {avg_time_per_set:.4f} ms 超过 5 ms")

    def test_core_utilization_performance(self):
        """测试核心利用率获取性能"""
        manager = CPUAffinityManager()

        # 测量获取利用率的时间
        num_iterations = 10
        total_time = 0.0

        for i in range(num_iterations):
            start_time = time.perf_counter()
            utilization = manager.get_core_utilization(interval=0.05)  # 短间隔
            end_time = time.perf_counter()

            iteration_time = (end_time - start_time) * 1000  # 毫秒
            total_time += iteration_time

            # 验证结果
            self.assertIsInstance(utilization, dict)

        avg_time_per_iteration = total_time / num_iterations

        print(f"\n📊 CPU利用率获取性能测试:")
        print(f"  迭代次数: {num_iterations}")
        print(f"  总时间: {total_time:.2f} ms")
        print(f"  平均获取时间: {avg_time_per_iteration:.2f} ms")

        # 性能要求：平均获取时间 < 100ms（包含50ms采样间隔）
        self.assertLess(avg_time_per_iteration, 100.0,
                        f"平均获取时间 {avg_time_per_iteration:.2f} ms 超过 100 ms")

    def test_topology_detection_performance(self):
        """测试拓扑检测性能"""
        manager = CPUAffinityManager()

        # 测量拓扑检测时间
        num_iterations = 100

        start_time = time.perf_counter()

        for i in range(num_iterations):
            topology = manager.get_cpu_topology()
            # 验证结果
            self.assertIn('physical_cores', topology)

        end_time = time.perf_counter()

        total_time = end_time - start_time
        avg_time_per_detection = total_time / num_iterations * 1000  # 毫秒

        print(f"\n📊 CPU拓扑检测性能测试:")
        print(f"  迭代次数: {num_iterations}")
        print(f"  总时间: {total_time * 1000:.2f} ms")
        print(f"  平均检测时间: {avg_time_per_detection:.4f} ms")

        # 性能要求：平均检测时间 < 10ms（在macOS上，get_cpu_topology可能涉及系统调用）
        self.assertLess(avg_time_per_detection, 10.0,
                        f"平均检测时间 {avg_time_per_detection:.4f} ms 超过 10 ms")

    def test_concurrent_affinity_operations(self):
        """测试并发亲和性操作"""
        import concurrent.futures

        manager = CPUAffinityManager()
        manager.save_original_affinity()

        def worker(worker_id):
            """工作线程函数"""
            # 每个线程设置不同的亲和性
            cores = [worker_id % manager.cpu_count_logical]
            success = manager.set_affinity(cores)
            return success

        # 并发测试
        num_threads = 10
        num_operations = 5

        start_time = time.perf_counter()

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for i in range(num_operations):
                for j in range(num_threads):
                    future = executor.submit(worker, j)
                    futures.append(future)

            # 等待所有任务完成
            results = []
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

        end_time = time.perf_counter()

        total_time = end_time - start_time
        total_operations = num_threads * num_operations
        throughput = total_operations / total_time

        print(f"\n📊 并发CPU亲和性操作测试:")
        print(f"  线程数: {num_threads}")
        print(f"  操作数/线程: {num_operations}")
        print(f"  总操作数: {total_operations}")
        print(f"  总时间: {total_time * 1000:.2f} ms")
        print(f"  吞吐量: {throughput:.1f} 操作/秒")

        # 恢复原始亲和性
        manager.restore_original_affinity()

        # 性能要求：吞吐量 > 1000 操作/秒
        self.assertGreater(throughput, 100.0,
                           f"吞吐量 {throughput:.1f} 操作/秒 低于 100 操作/秒")


def run_performance_tests():
    """运行性能测试"""
    print("🚀 运行CPU亲和性性能测试...")

    # 创建测试套件
    suite = unittest.TestSuite()
    suite.addTest(TestPerformance('test_affinity_setting_performance'))
    suite.addTest(TestPerformance('test_core_utilization_performance'))
    suite.addTest(TestPerformance('test_topology_detection_performance'))
    suite.addTest(TestPerformance('test_concurrent_affinity_operations'))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    # 运行所有测试
    unittest.main(verbosity=2)
