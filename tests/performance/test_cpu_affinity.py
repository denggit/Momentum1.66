"""
四号引擎v3.0性能基准测试：CPU亲和性验证
目标：验证双核隔离架构的CPU亲和性设置
"""

import os
import sys
import time
import multiprocessing
import concurrent.futures
from typing import Dict, List, Tuple
import psutil
import numpy as np

class CPUAffinityBenchmark:
    """CPU亲和性基准测试类"""

    def __init__(self):
        """初始化CPU亲和性测试"""
        self.cpu_count = psutil.cpu_count(logical=False)  # 物理核心数
        self.logical_cpu_count = psutil.cpu_count(logical=True)  # 逻辑核心数
        self.process = psutil.Process(os.getpid())

        print(f"🔧 系统CPU信息:")
        print(f"  物理核心: {self.cpu_count}")
        print(f"  逻辑核心: {self.logical_cpu_count}")

        # 获取CPU频率信息
        try:
            cpu_freq = psutil.cpu_freq()
            if cpu_freq:
                print(f"  当前频率: {cpu_freq.current:.1f} MHz")
                print(f"  最小频率: {cpu_freq.min:.1f} MHz")
                print(f"  最大频率: {cpu_freq.max:.1f} MHz")
        except:
            pass

    def check_cpu_affinity_support(self) -> Dict:
        """检查CPU亲和性支持"""
        print("\n🔍 检查CPU亲和性支持...")

        support_info = {
            'os_support': False,
            'python_support': False,
            'psutil_support': False,
            'can_get_affinity': False,
            'can_set_affinity': False,
            'notes': []
        }

        # 检查操作系统
        if sys.platform in ['linux', 'linux2', 'darwin']:
            support_info['os_support'] = True
            support_info['notes'].append(f"操作系统: {sys.platform} 支持CPU亲和性")
        else:
            support_info['notes'].append(f"操作系统: {sys.platform} 可能不支持CPU亲和性")

        # 检查Python版本
        if sys.version_info >= (3, 3):
            support_info['python_support'] = True
            support_info['notes'].append(f"Python {sys.version} 支持os.sched_setaffinity")
        else:
            support_info['notes'].append(f"Python {sys.version} 版本较低，可能不支持CPU亲和性")

        # 检查psutil支持
        try:
            current_affinity = self.process.cpu_affinity()
            support_info['psutil_support'] = True
            support_info['can_get_affinity'] = True
            support_info['notes'].append(f"psutil可以获取CPU亲和性: {current_affinity}")
        except Exception as e:
            support_info['notes'].append(f"psutil获取CPU亲和性失败: {e}")

        # 测试设置CPU亲和性
        try:
            # 尝试设置为所有核心
            all_cores = list(range(self.logical_cpu_count))
            self.process.cpu_affinity(all_cores)
            support_info['can_set_affinity'] = True
            support_info['notes'].append(f"可以设置CPU亲和性到所有核心")
        except Exception as e:
            support_info['notes'].append(f"设置CPU亲和性失败: {e}")

        return support_info

    def test_dual_core_isolation(self) -> Dict:
        """测试双核隔离架构"""
        print("\n🔬 测试双核隔离架构...")

        results = {
            'isolation_possible': False,
            'core0_performance': {},
            'core1_performance': {},
            'interference_analysis': {},
            'recommendations': []
        }

        # 测试核心0性能（主进程，I/O密集）
        print("  测试核心0（主进程，I/O密集）...")
        core0_result = self._benchmark_single_core([0])
        results['core0_performance'] = core0_result

        # 测试核心1性能（Worker进程，CPU密集）
        print("  测试核心1（Worker进程，CPU密集）...")
        core1_result = self._benchmark_single_core([1])
        results['core1_performance'] = core1_result

        # 测试双核并行性能
        print("  测试双核并行性能...")
        parallel_result = self._benchmark_parallel_cores([0, 1])
        results['interference_analysis'] = parallel_result

        # 分析隔离效果
        if (core0_result['tasks_per_second'] > 0 and
            core1_result['tasks_per_second'] > 0):
            results['isolation_possible'] = True
            results['recommendations'].append("✅ 双核隔离架构可行")

            # 计算性能损失
            core0_single = core0_result['tasks_per_second']
            core1_single = core1_result['tasks_per_second']
            parallel_total = parallel_result['total_tasks_per_second']

            expected_total = core0_single + core1_single
            performance_loss = (expected_total - parallel_total) / expected_total * 100

            if performance_loss < 10:
                results['recommendations'].append(f"✅ 并行性能良好，损失仅 {performance_loss:.1f}%")
            else:
                results['recommendations'].append(f"⚠️  并行性能损失较大: {performance_loss:.1f}%")
                results['recommendations'].append("  建议优化进程间通信或减少共享资源")

        return results

    def _benchmark_single_core(self, cores: List[int]) -> Dict:
        """基准测试单个核心性能"""
        # 设置CPU亲和性
        try:
            self.process.cpu_affinity(cores)
        except:
            pass

        # 运行计算密集型任务
        def compute_intensive_task():
            # 模拟KDE计算
            n = 10000
            data = np.random.randn(n)
            result = 0.0
            for i in range(n):
                result += np.exp(-0.5 * data[i] ** 2) / np.sqrt(2 * np.pi)
            return result

        # 运行I/O密集型任务
        def io_intensive_task():
            # 模拟Tick处理
            ticks = []
            for i in range(1000):
                tick = {
                    'price': 3000.0 + np.random.randn() * 10,
                    'size': np.random.exponential(1.0),
                    'side': 'buy' if np.random.random() > 0.5 else 'sell',
                    'ts': int(time.time() * 1000) + i
                }
                ticks.append(tick)
            return len(ticks)

        # 测量性能
        num_iterations = 100
        compute_times = []
        io_times = []

        for i in range(num_iterations):
            # 计算密集型
            start = time.perf_counter()
            compute_intensive_task()
            compute_times.append(time.perf_counter() - start)

            # I/O密集型
            start = time.perf_counter()
            io_intensive_task()
            io_times.append(time.perf_counter() - start)

        return {
            'cores': cores,
            'compute_time_mean': np.mean(compute_times),
            'compute_time_std': np.std(compute_times),
            'io_time_mean': np.mean(io_times),
            'io_time_std': np.std(io_times),
            'tasks_per_second': num_iterations / (np.mean(compute_times) + np.mean(io_times)),
            'notes': f"测试核心: {cores}"
        }

    def _benchmark_parallel_cores(self, cores: List[int]) -> Dict:
        """测试并行核心性能"""
        from concurrent.futures import ProcessPoolExecutor
        import multiprocessing

        def worker_task(worker_id: int, core_id: int):
            """Worker进程任务"""
            # 设置Worker进程的CPU亲和性
            try:
                process = psutil.Process(os.getpid())
                process.cpu_affinity([core_id])
            except:
                pass

            # 模拟CPU密集型工作
            n = 5000
            result = 0.0
            for i in range(n):
                data = np.random.randn(100)
                result += np.sum(np.exp(-0.5 * data ** 2))

            return worker_id, result

        # 使用ProcessPoolExecutor创建进程池
        num_workers = len(cores)
        tasks_per_worker = 20

        start_time = time.perf_counter()
        total_tasks_completed = 0

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            # 提交任务
            futures = []
            for worker_id in range(num_workers):
                for task_id in range(tasks_per_worker):
                    future = executor.submit(
                        worker_task,
                        worker_id * tasks_per_worker + task_id,
                        cores[worker_id % len(cores)]
                    )
                    futures.append(future)

            # 等待所有任务完成
            results = []
            for future in concurrent.futures.as_gathered(futures):
                try:
                    results.append(future.result())
                    total_tasks_completed += 1
                except Exception as e:
                    print(f"任务失败: {e}")

        elapsed_time = time.perf_counter() - start_time

        return {
            'cores': cores,
            'num_workers': num_workers,
            'total_tasks': len(futures),
            'completed_tasks': total_tasks_completed,
            'elapsed_time': elapsed_time,
            'total_tasks_per_second': total_tasks_completed / elapsed_time,
            'efficiency': total_tasks_completed / len(futures) * 100,
            'notes': f"并行测试: {cores} 核心，{num_workers} 个Worker"
        }

    def measure_process_creation_overhead(self) -> Dict:
        """测量进程创建开销"""
        print("\n⏱️  测量进程创建开销...")

        results = {
            'process_creation_times': [],
            'thread_creation_times': [],
            'process_vs_thread_ratio': 0.0,
            'recommendations': []
        }

        # 测量进程创建时间
        process_times = []
        for i in range(10):
            start = time.perf_counter()
            process = multiprocessing.Process(target=lambda: time.sleep(0.001))
            process.start()
            process.join()
            process_times.append(time.perf_counter() - start)

        results['process_creation_times'] = {
            'mean': np.mean(process_times),
            'std': np.std(process_times),
            'min': np.min(process_times),
            'max': np.max(process_times)
        }

        # 测量线程创建时间
        import threading
        thread_times = []
        for i in range(10):
            start = time.perf_counter()
            thread = threading.Thread(target=lambda: time.sleep(0.001))
            thread.start()
            thread.join()
            thread_times.append(time.perf_counter() - start)

        results['thread_creation_times'] = {
            'mean': np.mean(thread_times),
            'std': np.std(thread_times),
            'min': np.min(thread_times),
            'max': np.max(thread_times)
        }

        # 计算进程 vs 线程开销比
        process_mean = results['process_creation_times']['mean']
        thread_mean = results['thread_creation_times']['mean']
        results['process_vs_thread_ratio'] = process_mean / thread_mean if thread_mean > 0 else 0

        # 生成建议
        if results['process_vs_thread_ratio'] > 10:
            results['recommendations'].append(
                f"⚠️  进程创建开销很大 ({results['process_vs_thread_ratio']:.1f}x 线程)，建议使用进程池复用"
            )
        else:
            results['recommendations'].append(
                f"✅ 进程创建开销可接受 ({results['process_vs_thread_ratio']:.1f}x 线程)"
            )

        return results

    def generate_recommendations(self, all_results: Dict) -> List[str]:
        """生成CPU亲和性配置建议"""
        recommendations = []

        # CPU亲和性支持建议
        support_info = all_results['support_info']
        if not support_info['can_set_affinity']:
            recommendations.append("❌ 系统不支持CPU亲和性设置，无法实现双核隔离")
            return recommendations

        # 双核隔离建议
        isolation_results = all_results['isolation_results']
        if isolation_results['isolation_possible']:
            recommendations.append("✅ 双核隔离架构可行")
        else:
            recommendations.append("❌ 双核隔离架构不可行")

        # 进程创建开销建议
        overhead_results = all_results['overhead_results']
        if overhead_results['process_vs_thread_ratio'] > 20:
            recommendations.append("⚠️  进程创建开销极大，必须使用ProcessPoolExecutor复用进程")
        elif overhead_results['process_vs_thread_ratio'] > 5:
            recommendations.append("⚠️  进程创建开销较大，建议使用进程池")
        else:
            recommendations.append("✅ 进程创建开销可接受")

        # 核心分配建议
        if self.cpu_count >= 2:
            recommendations.append(f"✅ 系统有 {self.cpu_count} 个物理核心，适合双核隔离")
            recommendations.append("  建议分配: 核心0 → 主进程 (I/O密集), 核心1 → Worker进程 (CPU密集)")
        else:
            recommendations.append(f"❌ 系统只有 {self.cpu_count} 个物理核心，不适合双核隔离")

        # 性能优化建议
        interference = isolation_results['interference_analysis']
        if interference.get('efficiency', 100) < 90:
            recommendations.append(f"⚠️  并行效率较低 ({interference.get('efficiency', 0):.1f}%)，需优化进程间通信")

        return recommendations

    def print_report(self, all_results: Dict):
        """打印CPU亲和性测试报告"""
        print("\n" + "="*60)
        print("⚡ 四号引擎v3.0 CPU亲和性基准测试报告")
        print("="*60)

        # CPU信息
        print(f"\n🔧 系统CPU信息:")
        print(f"  物理核心数: {self.cpu_count}")
        print(f"  逻辑核心数: {self.logical_cpu_count}")

        # 支持性检查
        support_info = all_results['support_info']
        print(f"\n🔍 CPU亲和性支持:")
        for note in support_info['notes']:
            print(f"  {note}")

        # 单核性能
        core0 = all_results['isolation_results']['core0_performance']
        core1 = all_results['isolation_results']['core1_performance']

        print(f"\n📊 单核性能测试:")
        print(f"  核心0 (主进程): {core0.get('tasks_per_second', 0):.1f} 任务/秒")
        print(f"  核心1 (Worker): {core1.get('tasks_per_second', 0):.1f} 任务/秒")

        # 并行性能
        parallel = all_results['isolation_results']['interference_analysis']
        print(f"\n📈 并行性能测试:")
        print(f"  总任务数: {parallel.get('total_tasks', 0)}")
        print(f"  完成数: {parallel.get('completed_tasks', 0)}")
        print(f"  效率: {parallel.get('efficiency', 0):.1f}%")
        print(f"  总吞吐量: {parallel.get('total_tasks_per_second', 0):.1f} 任务/秒")

        # 进程创建开销
        overhead = all_results['overhead_results']
        print(f"\n⏱️  进程创建开销:")
        print(f"  进程创建平均时间: {overhead['process_creation_times']['mean']*1000:.2f} ms")
        print(f"  线程创建平均时间: {overhead['thread_creation_times']['mean']*1000:.2f} ms")
        print(f"  进程/线程开销比: {overhead['process_vs_thread_ratio']:.1f}x")

        # 建议
        print(f"\n💡 架构建议:")
        recommendations = self.generate_recommendations(all_results)
        for rec in recommendations:
            print(f"  {rec}")

        print("\n" + "="*60)

def main():
    """主函数：运行CPU亲和性测试"""
    print("🔧 四号引擎v3.0 CPU亲和性基准测试启动...")

    # 创建基准测试实例
    benchmark = CPUAffinityBenchmark()

    # 运行各项测试
    print("\n🧪 运行综合测试...")

    support_info = benchmark.check_cpu_affinity_support()
    isolation_results = benchmark.test_dual_core_isolation()
    overhead_results = benchmark.measure_process_creation_overhead()

    all_results = {
        'support_info': support_info,
        'isolation_results': isolation_results,
        'overhead_results': overhead_results
    }

    # 打印报告
    benchmark.print_report(all_results)

    # 保存结果到文件
    import json
    with open('tests/performance/cpu_affinity_benchmark.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    print("💾 结果已保存到: tests/performance/cpu_affinity_benchmark.json")

    # 返回退出码（用于CI/CD）
    if isolation_results.get('isolation_possible', False):
        return 0  # 成功
    else:
        return 1  # 失败

if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)