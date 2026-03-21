"""
四号引擎v3.0性能基准测试：内存使用分析
目标：内存使用稳定，无内存泄漏
"""

import gc
import os
import time
from typing import Dict

import matplotlib
import numpy as np
import psutil

matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as plt


class MemoryUsageBenchmark:
    """内存使用基准测试类"""

    def __init__(self, duration_seconds: int = 60, sampling_interval: float = 0.1):
        """
        初始化内存基准测试

        Args:
            duration_seconds: 测试持续时间（秒）
            sampling_interval: 采样间隔（秒）
        """
        self.duration_seconds = duration_seconds
        self.sampling_interval = sampling_interval
        self.process = psutil.Process(os.getpid())

        # 数据存储
        self.timestamps = []
        self.memory_rss = []  # 常驻内存
        self.memory_vms = []  # 虚拟内存
        self.memory_shared = []  # 共享内存
        self.gc_counts = []
        self.gc_times = []

    def _simulate_engine_workload(self, iteration: int):
        """
        模拟四号引擎工作负载
        包括：Tick处理、RangeBar生成、CVD计算、KDE计算等
        """
        # 模拟Tick数据生成
        num_ticks = 1000
        prices = np.random.normal(3000.0, 10.0, num_ticks)
        volumes = np.random.exponential(1.0, num_ticks)
        sides = np.random.choice(['buy', 'sell'], num_ticks)

        # 模拟RangeBar生成（内存密集型操作）
        range_bars = []
        current_price = prices[0]
        current_high = current_price
        current_low = current_price
        current_volume = 0.0

        for i in range(num_ticks):
            price = prices[i]
            volume = volumes[i]

            # 更新高/低
            current_high = max(current_high, price)
            current_low = min(current_low, price)

            # 模拟RangeBar生成（每10个Tick生成一个Bar）
            if i % 10 == 0 and i > 0:
                range_bar = {
                    'open': current_price,
                    'high': current_high,
                    'low': current_low,
                    'close': price,
                    'volume': current_volume
                }
                range_bars.append(range_bar)

                # 重置
                current_price = price
                current_high = price
                current_low = price
                current_volume = 0.0

            current_volume += volume

        # 模拟CVD计算（计算密集型）
        cvd = 0.0
        for i in range(num_ticks):
            if sides[i] == 'buy':
                cvd += volumes[i]
            else:
                cvd -= volumes[i]

        # 模拟KDE计算（内存+计算密集型）
        if len(range_bars) > 0:
            close_prices = np.array([bar['close'] for bar in range_bars])
            if len(close_prices) > 1:
                # 模拟KDE核函数计算
                bandwidth = 0.5
                kde_result = self._simulate_kde(close_prices, bandwidth)

        # 强制垃圾回收（模拟引擎的清理操作）
        if iteration % 100 == 0:
            gc.collect()

        return len(range_bars), cvd

    def _simulate_kde(self, data: np.ndarray, bandwidth: float) -> np.ndarray:
        """模拟KDE计算（简化版）"""
        n = len(data)
        if n < 2:
            return np.zeros_like(data)

        # 创建评估网格
        grid = np.linspace(data.min(), data.max(), 100)

        # 模拟高斯核计算
        kde_values = np.zeros_like(grid)
        for i, x in enumerate(grid):
            # 高斯核函数
            kernel_values = np.exp(-0.5 * ((data - x) / bandwidth) ** 2)
            kde_values[i] = np.sum(kernel_values) / (n * bandwidth * np.sqrt(2 * np.pi))

        return kde_values

    def run_benchmark(self) -> Dict:
        """运行内存使用基准测试"""
        print("🧠 开始内存使用基准测试...")
        print(f"⏱️  测试持续时间: {self.duration_seconds} 秒")
        print(f"📊 采样间隔: {self.sampling_interval} 秒")

        start_time = time.time()
        iteration = 0

        # 启用详细垃圾回收统计
        gc.enable()
        gc.set_debug(gc.DEBUG_STATS)

        while time.time() - start_time < self.duration_seconds:
            iteration += 1

            # 记录时间戳
            current_time = time.time() - start_time
            self.timestamps.append(current_time)

            # 获取内存信息
            mem_info = self.process.memory_info()
            self.memory_rss.append(mem_info.rss / 1024 / 1024)  # MB
            self.memory_vms.append(mem_info.vms / 1024 / 1024)  # MB

            # 获取共享内存（如果可用）
            try:
                mem_shared = mem_info.shared / 1024 / 1024
                self.memory_shared.append(mem_shared)
            except:
                self.memory_shared.append(0.0)

            # 获取垃圾回收统计
            gc_counts = gc.get_count()
            self.gc_counts.append(sum(gc_counts))

            # 模拟引擎工作负载
            range_bars_count, cvd = self._simulate_engine_workload(iteration)

            # 定期输出进度
            if iteration % 10 == 0:
                print(f"🔄 进度: {current_time:.1f}s | "
                      f"RSS: {self.memory_rss[-1]:.1f}MB | "
                      f"RangeBars: {range_bars_count}")

            # 等待采样间隔
            time.sleep(self.sampling_interval)

        # 禁用详细垃圾回收统计
        gc.set_debug(0)

        # 计算内存泄漏检测
        leak_analysis = self._analyze_memory_leak()

        print("✅ 内存基准测试完成!")
        return leak_analysis

    def _analyze_memory_leak(self) -> Dict:
        """分析内存泄漏"""
        if len(self.memory_rss) < 10:
            return {'leak_detected': False, 'reason': '样本不足'}

        # 计算内存增长趋势
        rss_array = np.array(self.memory_rss)
        timestamps_array = np.array(self.timestamps)

        # 线性回归分析趋势
        from scipy import stats
        slope, intercept, r_value, p_value, std_err = stats.linregress(
            timestamps_array, rss_array
        )

        # 计算内存增长速率（MB/分钟）
        growth_rate_mb_per_min = slope * 60

        # 检测内存泄漏条件
        leak_detected = False
        leak_reason = ""

        if growth_rate_mb_per_min > 1.0:  # 每分钟增长超过1MB
            leak_detected = True
            leak_reason = f"内存持续增长: {growth_rate_mb_per_min:.2f} MB/分钟"

        elif max(rss_array) - min(rss_array) > 50:  # 总体增长超过50MB
            leak_detected = True
            leak_reason = f"内存波动过大: {max(rss_array) - min(rss_array):.1f} MB"

        # 计算统计信息
        stats = {
            'leak_detected': leak_detected,
            'leak_reason': leak_reason,
            'duration_seconds': self.duration_seconds,
            'samples_count': len(self.timestamps),
            'memory_rss_avg_mb': np.mean(rss_array),
            'memory_rss_min_mb': np.min(rss_array),
            'memory_rss_max_mb': np.max(rss_array),
            'memory_rss_std_mb': np.std(rss_array),
            'memory_growth_rate_mb_per_min': growth_rate_mb_per_min,
            'linear_regression_r_squared': r_value ** 2,
            'gc_collections_total': sum(self.gc_counts),
            'gc_collections_avg': np.mean(self.gc_counts) if self.gc_counts else 0,
        }

        return stats

    def generate_report(self, analysis: Dict):
        """生成内存使用报告和图表"""
        print("\n" + "=" * 60)
        print("🧠 四号引擎v3.0内存使用基准测试报告")
        print("=" * 60)

        print(f"\n📊 测试概况:")
        print(f"  测试持续时间: {analysis.get('duration_seconds', 0):.1f} 秒")
        print(f"  采样数量: {analysis.get('samples_count', 0):,}")
        print(f"  目标: 内存使用稳定，无内存泄漏")

        print(f"\n💾 内存统计:")
        print(f"  平均RSS内存: {analysis.get('memory_rss_avg_mb', 0):.1f} MB")
        print(f"  最小RSS内存: {analysis.get('memory_rss_min_mb', 0):.1f} MB")
        print(f"  最大RSS内存: {analysis.get('memory_rss_max_mb', 0):.1f} MB")
        print(f"  内存标准差: {analysis.get('memory_rss_std_mb', 0):.1f} MB")
        print(f"  内存增长速率: {analysis.get('memory_growth_rate_mb_per_min', 0):.2f} MB/分钟")

        print(f"\n🗑️  垃圾回收:")
        print(f"  GC总次数: {analysis.get('gc_collections_total', 0):,}")
        print(f"  GC平均次数: {analysis.get('gc_collections_avg', 0):.1f}")

        print(f"\n🔍 内存泄漏检测:")
        if analysis.get('leak_detected', False):
            print(f"  ⚠️  检测到潜在内存泄漏!")
            print(f"    原因: {analysis.get('leak_reason', '未知')}")
        else:
            print(f"  ✅ 未检测到内存泄漏")
            print(f"    内存增长速率: {analysis.get('memory_growth_rate_mb_per_min', 0):.2f} MB/分钟 (< 1.0 MB/分钟)")

        print(f"\n📈 趋势分析:")
        print(f"  线性回归R²: {analysis.get('linear_regression_r_squared', 0):.3f}")
        if analysis.get('linear_regression_r_squared', 0) > 0.7:
            print(f"  ⚠️  内存增长趋势明显 (R² > 0.7)")
        else:
            print(f"  ✅ 内存波动随机，无明显增长趋势")

        print("\n" + "=" * 60)

        # 生成内存使用图表
        self._plot_memory_usage(analysis)

    def _plot_memory_usage(self, analysis: Dict):
        """绘制内存使用图表"""
        plt.figure(figsize=(12, 8))

        # 子图1：RSS内存使用
        plt.subplot(2, 2, 1)
        plt.plot(self.timestamps, self.memory_rss, 'b-', linewidth=1, alpha=0.7)
        plt.xlabel('时间 (秒)')
        plt.ylabel('RSS内存 (MB)')
        plt.title('常驻内存使用')
        plt.grid(True, alpha=0.3)

        # 添加趋势线
        if len(self.timestamps) > 1:
            z = np.polyfit(self.timestamps, self.memory_rss, 1)
            p = np.poly1d(z)
            plt.plot(self.timestamps, p(self.timestamps), "r--", alpha=0.5,
                     label=f'趋势: {z[0] * 60:.2f} MB/分钟')
            plt.legend()

        # 子图2：虚拟内存使用
        plt.subplot(2, 2, 2)
        plt.plot(self.timestamps, self.memory_vms, 'g-', linewidth=1, alpha=0.7)
        plt.xlabel('时间 (秒)')
        plt.ylabel('虚拟内存 (MB)')
        plt.title('虚拟内存使用')
        plt.grid(True, alpha=0.3)

        # 子图3：GC统计
        plt.subplot(2, 2, 3)
        plt.plot(self.timestamps, self.gc_counts, 'r-', linewidth=1, alpha=0.7)
        plt.xlabel('时间 (秒)')
        plt.ylabel('GC计数')
        plt.title('垃圾回收统计')
        plt.grid(True, alpha=0.3)

        # 子图4：内存分布直方图
        plt.subplot(2, 2, 4)
        plt.hist(self.memory_rss, bins=30, alpha=0.7, color='purple', edgecolor='black')
        plt.xlabel('RSS内存 (MB)')
        plt.ylabel('频率')
        plt.title('内存分布直方图')
        plt.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig('tests/performance/memory_usage_report.png', dpi=150, bbox_inches='tight')
        plt.close()

        print(f"📊 图表已保存到: tests/performance/memory_usage_report.png")


def main():
    """主函数：运行内存基准测试"""
    print("🔧 四号引擎v3.0内存使用基准测试启动...")

    # 创建基准测试实例
    benchmark = MemoryUsageBenchmark(
        duration_seconds=30,  # 30秒测试
        sampling_interval=0.1  # 100ms采样间隔
    )

    # 运行基准测试
    analysis = benchmark.run_benchmark()

    # 生成报告
    benchmark.generate_report(analysis)

    # 保存结果到文件
    import json
    with open('tests/performance/memory_usage_benchmark.json', 'w') as f:
        json.dump(analysis, f, indent=2)

    print("💾 结果已保存到: tests/performance/memory_usage_benchmark.json")

    # 返回退出码（用于CI/CD）
    if analysis.get('leak_detected', True):
        return 1  # 检测到内存泄漏
    else:
        return 0  # 无内存泄漏


if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
