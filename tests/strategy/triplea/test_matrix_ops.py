"""
四号引擎v3.0 矩阵操作工具测试
测试矩阵广播、滑动窗口、数值计算等工具函数
"""

import unittest
import sys
import os
import time
import numpy as np

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.strategy.triplea.matrix_ops import (
    broadcast_to_match,
    sliding_window_view,
    nanmean_axis0,
    nanstd_axis0,
    rolling_mean,
    rolling_std,
    matrix_broadcast_kde,
    numba_broadcast_kde,
    compute_returns,
    normalize_matrix,
    fast_quantile,
    measure_performance,
    get_performance_stats
)


class TestMatrixOps(unittest.TestCase):
    """测试矩阵操作工具"""

    def setUp(self):
        """设置测试环境"""
        np.random.seed(42)  # 可重复测试

    def test_broadcast_to_match(self):
        """测试数组广播匹配"""
        # 测试1：相同形状
        a = np.array([1, 2, 3])
        b = np.array([4, 5, 6])
        a_broadcast, b_broadcast = broadcast_to_match(a, b)
        np.testing.assert_array_equal(a_broadcast, a)
        np.testing.assert_array_equal(b_broadcast, b)

        # 测试2：广播标量
        a = np.array([1, 2, 3])
        b = 5
        a_broadcast, b_broadcast = broadcast_to_match(a, b)
        np.testing.assert_array_equal(a_broadcast, a)
        np.testing.assert_array_equal(b_broadcast, np.array([5, 5, 5]))

        # 测试3：不同维度
        a = np.array([[1, 2], [3, 4]])  # 2x2
        b = np.array([10, 20])  # 2
        a_broadcast, b_broadcast = broadcast_to_match(a, b)
        expected_b = np.array([[10, 20], [10, 20]])
        np.testing.assert_array_equal(b_broadcast, expected_b)

        # 测试4：指定轴广播
        a = np.array([1, 2, 3])  # 3
        b = np.array([[1], [2], [3]])  # 3x1
        a_broadcast, b_broadcast = broadcast_to_match(a, b, axis=1)
        expected_a = np.array([[1, 2, 3], [1, 2, 3], [1, 2, 3]])
        np.testing.assert_array_equal(a_broadcast, expected_a)

    def test_sliding_window_view(self):
        """测试滑动窗口视图"""
        # 测试1：一维数组
        x = np.array([1, 2, 3, 4, 5])
        window_size = 3
        step = 1

        result = sliding_window_view(x, window_size, step)
        expected = np.array([[1, 2, 3], [2, 3, 4], [3, 4, 5]])
        np.testing.assert_array_equal(result, expected)

        # 测试2：步长大于1
        x = np.array([1, 2, 3, 4, 5, 6, 7])
        window_size = 4
        step = 2

        result = sliding_window_view(x, window_size, step)
        expected = np.array([[1, 2, 3, 4], [3, 4, 5, 6]])
        np.testing.assert_array_equal(result, expected)

        # 测试3：二维数组
        x = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
        window_size = 2
        step = 1

        result = sliding_window_view(x, window_size, step, axis=0)
        expected = np.array([
            [[1, 2, 3], [4, 5, 6]],
            [[4, 5, 6], [7, 8, 9]]
        ])
        np.testing.assert_array_equal(result, expected)

        # 测试4：检查是否只读
        x = np.array([1, 2, 3, 4])
        result = sliding_window_view(x, window_size=2)
        with self.assertRaises(ValueError):
            result[0, 0] = 99  # 应该失败，因为是只读视图

    def test_nanmean_axis0(self):
        """测试忽略NaN的均值计算"""
        # 测试1：无NaN数据
        x = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float64)
        result = nanmean_axis0(x)
        expected = np.mean(x, axis=0)
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

        # 测试2：有NaN数据
        x = np.array([[1, 2, np.nan], [4, np.nan, 6], [7, 8, 9]], dtype=np.float64)
        result = nanmean_axis0(x)
        expected = np.array([4.0, 5.0, 7.5])  # (1+4+7)/3=4, (2+8)/2=5, (6+9)/2=7.5
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

        # 测试3：全NaN数据
        x = np.array([[np.nan, np.nan], [np.nan, np.nan]], dtype=np.float64)
        result = nanmean_axis0(x)
        expected = np.array([np.nan, np.nan])
        np.testing.assert_array_equal(np.isnan(result), np.isnan(expected))

    def test_nanstd_axis0(self):
        """测试忽略NaN的标准差计算"""
        # 测试1：无NaN数据
        x = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float64)
        result = nanstd_axis0(x)
        expected = np.std(x, axis=0, ddof=1)
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

        # 测试2：有NaN数据
        x = np.array([[1, 2, np.nan], [4, np.nan, 6], [7, 8, 9]], dtype=np.float64)
        result = nanstd_axis0(x)
        # 手动计算
        col1 = np.array([1, 4, 7])
        col2 = np.array([2, 8])
        col3 = np.array([6, 9])
        expected = np.array([np.std(col1, ddof=1),
                            np.std(col2, ddof=1),
                            np.std(col3, ddof=1)])
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

    def test_rolling_mean(self):
        """测试滚动均值"""
        # 测试1：简单数据
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        window = 3
        min_periods = 1

        result = rolling_mean(x, window, min_periods)
        expected = np.array([np.nan, np.nan, 2.0, 3.0, 4.0, 5.0, 6.0])
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

        # 测试2：有NaN数据
        x = np.array([1.0, np.nan, 3.0, 4.0, np.nan, 6.0, 7.0])
        window = 3
        min_periods = 2  # 至少需要2个有效值

        result = rolling_mean(x, window, min_periods)
        # 窗口[1, nan, 3]: 有2个有效值，均值=2.0
        # 窗口[nan, 3, 4]: 有2个有效值，均值=3.5
        # 窗口[3, 4, nan]: 有2个有效值，均值=3.5
        # 窗口[4, nan, 6]: 有2个有效值，均值=5.0
        # 窗口[nan, 6, 7]: 有2个有效值，均值=6.5
        expected = np.array([np.nan, np.nan, 2.0, 3.5, 3.5, 5.0, 6.5])
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

        # 测试3：性能测试
        x = np.random.randn(10000)
        window = 50

        start = time.perf_counter()
        result = rolling_mean(x, window)
        elapsed = time.perf_counter() - start

        print(f"\n⏱️  滚动均值性能测试:")
        print(f"  数据大小: {len(x)}")
        print(f"  窗口大小: {window}")
        print(f"  计算时间: {elapsed*1000:.1f}ms")
        print(f"  结果非NaN数量: {np.sum(~np.isnan(result))}")

        self.assertLess(elapsed, 1.0, "滚动均值计算时间过长")

    def test_rolling_std(self):
        """测试滚动标准差"""
        # 测试1：简单数据
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        window = 3
        min_periods = 2

        result = rolling_std(x, window, min_periods)
        # 手动计算
        # 窗口[1,2,3]: 标准差=1.0
        # 窗口[2,3,4]: 标准差=1.0
        # 窗口[3,4,5]: 标准差=1.0
        expected = np.array([np.nan, np.nan, 1.0, 1.0, 1.0])
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

        # 测试2：性能测试
        x = np.random.randn(5000)
        window = 20

        start = time.perf_counter()
        result = rolling_std(x, window)
        elapsed = time.perf_counter() - start

        print(f"\n⏱️  滚动标准差性能测试:")
        print(f"  数据大小: {len(x)}")
        print(f"  窗口大小: {window}")
        print(f"  计算时间: {elapsed*1000:.1f}ms")

        self.assertLess(elapsed, 0.5, "滚动标准差计算时间过长")

    def test_matrix_broadcast_kde(self):
        """测试矩阵广播KDE计算"""
        # 测试1：简单数据
        prices = np.array([3000.0, 3001.0, 3002.0, 2999.0, 3003.0])
        grid_points = np.array([2998.0, 3000.0, 3002.0])
        bandwidth = 0.5

        result = matrix_broadcast_kde(prices, grid_points, bandwidth)

        # 检查结果形状
        self.assertEqual(result.shape, (3,))

        # 检查结果性质
        self.assertTrue(np.all(result >= 0), "KDE值应该非负")
        self.assertTrue(np.all(np.isfinite(result)), "KDE值应该有限")

        # 测试2：与简单循环实现比较
        n = len(prices)
        m = len(grid_points)
        kde_manual = np.zeros(m)

        for j in range(m):
            kernel_sum = 0.0
            for i in range(n):
                diff = prices[i] - grid_points[j]
                kernel_sum += np.exp(-0.5 * (diff / bandwidth) ** 2)
            kde_manual[j] = kernel_sum / (n * bandwidth * np.sqrt(2 * np.pi))

        np.testing.assert_array_almost_equal(result, kde_manual, decimal=10)

        # 测试3：性能测试
        prices_large = np.random.randn(10000) * 50 + 3000
        grid_large = np.linspace(2800, 3200, 500)

        start = time.perf_counter()
        result_large = matrix_broadcast_kde(prices_large, grid_large, 0.5)
        elapsed = time.perf_counter() - start

        print(f"\n⏱️  矩阵广播KDE性能测试:")
        print(f"  价格数据: {len(prices_large)}")
        print(f"  网格点数: {len(grid_large)}")
        print(f"  计算时间: {elapsed*1000:.1f}ms")
        print(f"  结果范围: {result_large.min():.2e} - {result_large.max():.2e}")

        self.assertLess(elapsed, 0.1, "矩阵广播KDE计算时间过长")

    def test_numba_broadcast_kde(self):
        """测试Numba加速KDE计算"""
        # 测试1：与纯Python版本结果一致
        prices = np.random.randn(100) * 50 + 3000
        grid_points = np.linspace(2900, 3100, 200)
        bandwidth = 0.5

        python_result = matrix_broadcast_kde(prices, grid_points, bandwidth)
        numba_result = numba_broadcast_kde(prices, grid_points, bandwidth)

        np.testing.assert_array_almost_equal(python_result, numba_result, decimal=10)

        # 测试2：性能对比
        prices_large = np.random.randn(5000) * 50 + 3000
        grid_large = np.linspace(2900, 3100, 300)

        # Python版本
        start = time.perf_counter()
        python_result = matrix_broadcast_kde(prices_large, grid_large, 0.5)
        python_time = time.perf_counter() - start

        # Numba版本
        start = time.perf_counter()
        numba_result = numba_broadcast_kde(prices_large, grid_large, 0.5)
        numba_time = time.perf_counter() - start

        print(f"\n⚡ KDE计算性能对比:")
        print(f"  Python版本: {python_time*1000:.1f}ms")
        print(f"  Numba版本: {numba_time*1000:.1f}ms")
        print(f"  加速比: {python_time/numba_time:.1f}x")

        # Numba应该更快
        if python_time > 0.001:  # 只有时间可测量时才检查
            self.assertLess(numba_time, python_time * 2, "Numba版本应该更快")

    def test_compute_returns(self):
        """测试收益率计算"""
        # 测试1：简单价格序列
        prices = np.array([100.0, 101.0, 102.0, 100.5, 99.0])
        periods = 1

        result = compute_returns(prices, periods)
        expected = np.array([0.01, 0.00990099, -0.01470588, -0.01492537])
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

        # 测试2：多期收益率
        prices = np.array([100.0, 101.0, 103.0, 106.0, 110.0])
        periods = 2

        result = compute_returns(prices, periods)
        expected = np.array([0.03, 0.04950495, 0.06796117])  # (103-100)/100, (106-101)/101, (110-103)/103
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

        # 测试3：边界情况
        prices = np.array([100.0])
        result = compute_returns(prices, periods=1)
        self.assertEqual(len(result), 0)

    def test_normalize_matrix(self):
        """测试矩阵归一化"""
        # 测试1：z-score归一化
        matrix = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float64)
        result = normalize_matrix(matrix, method='zscore', axis=0)

        # 检查每列均值为0，标准差为1
        col_means = np.mean(result, axis=0)
        col_stds = np.std(result, axis=0, ddof=1)
        np.testing.assert_array_almost_equal(col_means, [0, 0, 0], decimal=6)
        np.testing.assert_array_almost_equal(col_stds, [1, 1, 1], decimal=6)

        # 测试2：min-max归一化
        result = normalize_matrix(matrix, method='minmax', axis=0)
        # 每列应该在[0, 1]范围内
        self.assertTrue(np.all(result >= 0))
        self.assertTrue(np.all(result <= 1))
        # 每列最小值应为0，最大值应为1
        col_mins = np.min(result, axis=0)
        col_maxs = np.max(result, axis=0)
        np.testing.assert_array_almost_equal(col_mins, [0, 0, 0], decimal=6)
        np.testing.assert_array_almost_equal(col_maxs, [1, 1, 1], decimal=6)

        # 测试3：robust归一化（中位数和IQR）
        matrix_with_outliers = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9], [100, 200, 300]], dtype=np.float64)
        result = normalize_matrix(matrix_with_outliers, method='robust', axis=0)

        # 检查每列中位数接近0
        col_medians = np.median(result, axis=0)
        np.testing.assert_array_almost_equal(col_medians, [0, 0, 0], decimal=3)

        # 测试4：处理全零列
        matrix_zero_col = np.array([[0, 1], [0, 2], [0, 3]], dtype=np.float64)
        result = normalize_matrix(matrix_zero_col, method='zscore', axis=0)
        # 第一列应该保持为0（因为std=0）
        np.testing.assert_array_equal(result[:, 0], [0, 0, 0])

    def test_fast_quantile(self):
        """测试快速分位数计算"""
        # 测试1：简单数组
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        q = 0.5

        result = fast_quantile(data, q)
        expected = 3.0  # 中位数
        self.assertAlmostEqual(result, expected, places=6)

        # 测试2：多个分位数
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        test_cases = [(0.25, 3.25), (0.5, 5.5), (0.75, 7.75), (0.9, 9.1)]

        for q, expected in test_cases:
            result = fast_quantile(data, q)
            self.assertAlmostEqual(result, expected, places=6)

        # 测试3：有NaN数据
        data = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
        clean_data = data[~np.isnan(data)]
        q = 0.5

        result = fast_quantile(clean_data, q)
        expected = 3.0  # [1,2,4,5]的中位数是(2+4)/2=3
        self.assertAlmostEqual(result, expected, places=6)

        # 测试4：性能测试
        data_large = np.random.randn(100000)
        q_values = [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]

        start = time.perf_counter()
        for q in q_values:
            result = fast_quantile(data_large, q)
        elapsed = time.perf_counter() - start

        print(f"\n⏱️  快速分位数性能测试:")
        print(f"  数据大小: {len(data_large)}")
        print(f"  计算 {len(q_values)} 个分位数")
        print(f"  总时间: {elapsed*1000:.1f}ms")

        self.assertLess(elapsed, 0.5, "分位数计算时间过长")

    def test_performance_monitoring(self):
        """测试性能监控装饰器"""
        # 创建测试函数
        @measure_performance
        def slow_function(n):
            time.sleep(0.001 * n)  # 模拟工作负载
            return n * 2

        # 多次调用
        for i in range(5):
            result = slow_function(i)
            self.assertEqual(result, i * 2)

        # 获取性能统计
        stats = get_performance_stats(slow_function)

        # 检查统计信息
        self.assertEqual(stats['call_count'], 5)
        self.assertGreater(stats['total_time'], 0)
        self.assertGreater(stats['avg_time'], 0)
        self.assertGreater(stats['max_time'], 0)
        self.assertGreaterEqual(stats['min_time'], 0)

        print(f"\n📊 性能监控测试:")
        print(f"  调用次数: {stats['call_count']}")
        print(f"  总时间: {stats['total_time']*1000:.1f}ms")
        print(f"  平均时间: {stats['avg_time']*1000:.1f}ms")
        print(f"  最长时间: {stats['max_time']*1000:.1f}ms")
        print(f"  最短时间: {stats['min_time']*1000:.1f}ms")


class TestPerformance(unittest.TestCase):
    """性能测试"""

    def test_large_scale_broadcast(self):
        """测试大规模矩阵广播性能"""
        print("\n🚀 大规模矩阵广播性能测试")

        # 创建大规模数据
        n_samples = 10000
        n_features = 100
        n_grid = 500

        data = np.random.randn(n_samples, n_features)
        grid = np.random.randn(n_grid)

        # 广播测试
        start = time.perf_counter()
        for i in range(n_features):
            # 模拟列与网格的广播
            col = data[:, i]
            diff = col[:, np.newaxis] - grid
            kernel = np.exp(-0.5 * diff ** 2)
            result = np.mean(kernel, axis=0)
        elapsed = time.perf_counter() - start

        print(f"  数据形状: {data.shape}")
        print(f"  网格大小: {grid.shape}")
        print(f"  特征数量: {n_features}")
        print(f"  总时间: {elapsed:.2f}s")
        print(f"  平均每特征: {elapsed/n_features*1000:.1f}ms")

        self.assertLess(elapsed, 10.0, "大规模广播计算时间过长")

    def test_memory_efficient_sliding_window(self):
        """测试内存高效的滑动窗口"""
        print("\n💾 内存高效滑动窗口测试")

        # 创建大数据
        n = 100000
        window_size = 100
        x = np.random.randn(n)

        # 使用stride_tricks创建视图（无数据复制）
        start = time.perf_counter()
        windows = sliding_window_view(x, window_size, step=1)
        view_time = time.perf_counter() - start

        # 计算窗口统计（应该很快，因为只是视图）
        start = time.perf_counter()
        window_means = np.mean(windows, axis=1)
        compute_time = time.perf_counter() - start

        print(f"  数据大小: {n}")
        print(f"  窗口大小: {window_size}")
        print(f"  窗口数量: {len(windows)}")
        print(f"  创建视图时间: {view_time*1000:.1f}ms")
        print(f"  计算均值时间: {compute_time*1000:.1f}ms")
        print(f"  窗口内存使用（估计）: {windows.nbytes / (1024*1024):.1f} MB")

        # 检查结果
        self.assertEqual(len(window_means), n - window_size + 1)
        self.assertTrue(np.all(np.isfinite(window_means)))

        self.assertLess(view_time + compute_time, 1.0, "滑动窗口计算时间过长")


def run_performance_tests():
    """运行性能测试"""
    print("\n🚀 运行矩阵操作工具性能测试...")

    # 创建测试套件
    suite = unittest.TestSuite()
    suite.addTest(TestPerformance('test_large_scale_broadcast'))
    suite.addTest(TestPerformance('test_memory_efficient_sliding_window'))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    print("🔬 矩阵操作工具测试")
    print("=" * 50)

    # 运行所有测试
    unittest.main(verbosity=2)