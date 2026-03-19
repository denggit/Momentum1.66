"""
四号引擎v3.0 JIT编译监控器
监控Numba JIT编译性能，提供统计、告警和优化建议
"""

import time
import logging
import threading
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Callable
from collections import deque, defaultdict
import warnings

# 尝试导入numba相关模块
try:
    from numba.core.dispatcher import Dispatcher
    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False


class CompilePhase(Enum):
    """编译阶段枚举"""
    TYPE_INFERENCE = "type_inference"
    IR_GENERATION = "ir_generation"
    OPTIMIZATION = "optimization"
    CODE_GENERATION = "code_generation"
    TOTAL = "total"


class PerformanceLevel(Enum):
    """性能级别枚举"""
    EXCELLENT = "excellent"      # < 10ms
    GOOD = "good"                # 10-50ms
    ACCEPTABLE = "acceptable"    # 50-200ms
    SLOW = "slow"                # 200-500ms
    CRITICAL = "critical"        # > 500ms


@dataclass
class CompileMetrics:
    """编译指标"""
    function_name: str
    compile_time: float  # 总编译时间（秒）
    phase_times: Dict[CompilePhase, float] = field(default_factory=dict)
    cache_hit: bool = False
    cache_source: Optional[str] = None  # 'disk', 'memory', None
    memory_usage_bytes: Optional[int] = None
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    error_message: Optional[str] = None


@dataclass
class PerformanceStats:
    """性能统计"""
    function_name: str
    total_compilations: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    total_compile_time: float = 0.0
    avg_compile_time: float = 0.0
    min_compile_time: float = float('inf')
    max_compile_time: float = 0.0
    std_compile_time: float = 0.0
    recent_times: List[float] = field(default_factory=list)
    last_compilation: Optional[float] = None
    performance_level: PerformanceLevel = PerformanceLevel.EXCELLENT


@dataclass
class AlertThreshold:
    """告警阈值"""
    compile_time_ms: float = 500.0  # 编译时间阈值（毫秒）
    memory_mb: float = 100.0  # 内存使用阈值（MB）
    error_rate: float = 0.1  # 错误率阈值
    consecutive_failures: int = 3  # 连续失败次数
    cache_miss_rate: float = 0.5  # 缓存未命中率阈值


class JITMonitor:
    """
    JIT编译监控器

    主要功能：
    1. 监控Numba JIT编译性能指标
    2. 统计编译时间、缓存命中率等关键指标
    3. 提供实时性能告警和优化建议
    4. 支持多函数性能对比分析
    5. 生成性能报告和趋势分析

    使用示例：
    ```python
    # 创建监控器
    monitor = JITMonitor(
        enable_phase_tracking=True,
        alert_threshold=AlertThreshold(compile_time_ms=200.0)
    )

    # 开始监控
    monitor.start_monitoring()

    # 注册函数进行监控
    @monitor.track_function(critical=True)
    @njit(cache=True)
    def compute_kde(prices, bandwidth):
        pass

    # 手动记录编译事件
    monitor.record_compile_event(
        function_name="compute_kde",
        compile_time=0.125,
        cache_hit=False
    )

    # 获取性能报告
    report = monitor.get_performance_report()

    # 停止监控
    monitor.stop_monitoring()
    ```
    """

    def __init__(
        self,
        enable_phase_tracking: bool = False,
        alert_threshold: Optional[AlertThreshold] = None,
        max_history_size: int = 1000,
        logger: Optional[logging.Logger] = None
    ):
        """
        初始化JIT监控器

        Args:
            enable_phase_tracking: 是否启用编译阶段跟踪
            alert_threshold: 告警阈值配置
            max_history_size: 最大历史记录数
            logger: 日志记录器
        """
        self.enable_phase_tracking = enable_phase_tracking
        self.alert_threshold = alert_threshold or AlertThreshold()
        self.max_history_size = max_history_size

        self.logger = logger or logging.getLogger(__name__)
        self._is_monitoring = False
        self._is_shutdown = False
        self._lock = threading.RLock()

        # 性能数据存储
        self._compile_history: List[CompileMetrics] = []
        self._function_stats: Dict[str, PerformanceStats] = {}
        self._recent_metrics: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )

        # 告警系统
        self._alerts: List[Dict[str, Any]] = []
        self._alert_callbacks: List[Callable] = []

        # 性能基准
        self._performance_baseline: Dict[str, float] = {}

        # 检查Numba可用性
        if not NUMBA_AVAILABLE:
            self.logger.warning(
                "Numba不可用，JIT监控器将仅记录基本指标"
            )

    def start_monitoring(self) -> bool:
        """
        开始监控

        Returns:
            是否成功启动
        """
        if self._is_monitoring:
            self.logger.warning("监控已在运行中")
            return False

        if self._is_shutdown:
            self.logger.error("监控器已关闭，无法重新启动")
            return False

        self._is_monitoring = True
        self.logger.info("JIT编译监控器已启动")

        # 初始化性能基准
        self._initialize_baseline()

        return True

    def stop_monitoring(self) -> bool:
        """
        停止监控

        Returns:
            是否成功停止
        """
        if not self._is_monitoring:
            self.logger.warning("监控器未在运行")
            return False

        self._is_monitoring = False
        self.logger.info("JIT编译监控器已停止")

        # 生成最终报告
        self._generate_final_report()

        return True

    def _initialize_baseline(self) -> None:
        """初始化性能基准"""
        # 如果有历史数据，计算基准
        if self._compile_history:
            for func_name, stats in self._function_stats.items():
                if stats.total_compilations > 10:
                    self._performance_baseline[func_name] = stats.avg_compile_time
        else:
            # 默认基准值（毫秒）
            self._performance_baseline = {
                "fast_function": 10.0 / 1000.0,  # 10ms
                "medium_function": 50.0 / 1000.0,  # 50ms
                "slow_function": 200.0 / 1000.0,  # 200ms
            }

    def _generate_final_report(self) -> None:
        """生成最终报告"""
        if not self._compile_history:
            self.logger.debug("无编译历史记录，跳过最终报告")
            return

        # 获取性能报告
        report = self.get_performance_report()

        # 记录摘要
        self.logger.info(
            f"JIT编译监控最终报告: {report['total_functions_monitored']} 个函数, "
            f"{report['total_compilations']} 次编译, "
            f"缓存命中率: {report['cache_hit_rate']:.1%}"
        )

        # 记录性能摘要
        perf_summary = report['performance_summary']
        self.logger.info(
            f"性能分布: 优秀 {perf_summary['excellent']}, "
            f"良好 {perf_summary['good']}, "
            f"可接受 {perf_summary['acceptable']}, "
            f"慢 {perf_summary['slow']}, "
            f"严重 {perf_summary['critical']}"
        )

    def record_compile_event(
        self,
        function_name: str,
        compile_time: float,
        cache_hit: bool = False,
        cache_source: Optional[str] = None,
        phase_times: Optional[Dict[CompilePhase, float]] = None,
        memory_usage_bytes: Optional[int] = None,
        success: bool = True,
        error_message: Optional[str] = None
    ) -> str:
        """
        记录编译事件

        Args:
            function_name: 函数名称
            compile_time: 编译时间（秒）
            cache_hit: 是否缓存命中
            cache_source: 缓存来源 ('disk', 'memory', None)
            phase_times: 各阶段编译时间
            memory_usage_bytes: 内存使用字节数
            success: 是否成功
            error_message: 错误信息

        Returns:
            事件ID
        """
        if not self._is_monitoring:
            self.logger.debug("监控器未运行，跳过记录")
            return ""

        with self._lock:
            # 创建指标对象
            metrics = CompileMetrics(
                function_name=function_name,
                compile_time=compile_time,
                phase_times=phase_times or {},
                cache_hit=cache_hit,
                cache_source=cache_source,
                memory_usage_bytes=memory_usage_bytes,
                timestamp=time.time(),
                success=success,
                error_message=error_message
            )

            # 添加到历史记录
            self._compile_history.append(metrics)
            if len(self._compile_history) > self.max_history_size:
                self._compile_history.pop(0)

            # 更新函数统计
            self._update_function_stats(function_name, metrics)

            # 添加到最近指标
            self._recent_metrics[function_name].append(metrics)

            # 检查告警条件
            self._check_alert_conditions(metrics)

            # 生成事件ID
            event_id = f"{function_name}_{int(time.time()*1000)}"
            return event_id

    def _update_function_stats(
        self,
        function_name: str,
        metrics: CompileMetrics
    ) -> None:
        """更新函数统计信息"""
        if function_name not in self._function_stats:
            self._function_stats[function_name] = PerformanceStats(
                function_name=function_name
            )

        stats = self._function_stats[function_name]

        # 更新基本统计
        stats.total_compilations += 1
        stats.total_compile_time += metrics.compile_time

        if metrics.cache_hit:
            stats.cache_hits += 1
        else:
            stats.cache_misses += 1

        # 更新极值
        stats.min_compile_time = min(stats.min_compile_time, metrics.compile_time)
        stats.max_compile_time = max(stats.max_compile_time, metrics.compile_time)

        # 更新平均值
        if stats.total_compilations > 0:
            stats.avg_compile_time = (
                stats.total_compile_time / stats.total_compilations
            )

        # 更新标准差（使用最近数据）
        recent_times = [m.compile_time for m in self._recent_metrics[function_name]]
        if len(recent_times) >= 2:
            try:
                stats.std_compile_time = statistics.stdev(recent_times)
            except (statistics.StatisticsError, ValueError):
                stats.std_compile_time = 0.0

        # 更新最后编译时间
        stats.last_compilation = metrics.timestamp

        # 评估性能级别
        stats.performance_level = self._evaluate_performance_level(
            stats.avg_compile_time
        )

        stats.recent_times = recent_times[-10:]  # 保留最近10次

    def _evaluate_performance_level(self, avg_compile_time: float) -> PerformanceLevel:
        """评估性能级别"""
        compile_time_ms = avg_compile_time * 1000

        if compile_time_ms < 10:
            return PerformanceLevel.EXCELLENT
        elif compile_time_ms < 50:
            return PerformanceLevel.GOOD
        elif compile_time_ms < 200:
            return PerformanceLevel.ACCEPTABLE
        elif compile_time_ms < 500:
            return PerformanceLevel.SLOW
        else:
            return PerformanceLevel.CRITICAL

    def _check_alert_conditions(self, metrics: CompileMetrics) -> None:
        """检查告警条件"""
        # 检查编译时间告警
        compile_time_ms = metrics.compile_time * 1000
        if compile_time_ms > self.alert_threshold.compile_time_ms:
            self._trigger_alert(
                level="warning",
                type="compile_time_exceeded",
                message=f"编译时间过长: {compile_time_ms:.1f}ms > "
                       f"{self.alert_threshold.compile_time_ms:.1f}ms",
                function_name=metrics.function_name,
                value=compile_time_ms,
                threshold=self.alert_threshold.compile_time_ms
            )

        # 检查内存使用告警
        if metrics.memory_usage_bytes is not None:
            memory_mb = metrics.memory_usage_bytes / (1024 * 1024)
            if memory_mb > self.alert_threshold.memory_mb:
                self._trigger_alert(
                    level="warning",
                    type="memory_usage_exceeded",
                    message=f"内存使用过高: {memory_mb:.1f}MB > "
                           f"{self.alert_threshold.memory_mb:.1f}MB",
                    function_name=metrics.function_name,
                    value=memory_mb,
                    threshold=self.alert_threshold.memory_mb
                )

        # 检查错误率告警
        if not metrics.success:
            # 计算最近错误率
            recent_events = self._recent_metrics.get(metrics.function_name, deque())
            if len(recent_events) >= max(3, self.alert_threshold.consecutive_failures):
                error_count = sum(1 for e in recent_events if not e.success)
                error_rate = error_count / len(recent_events)

                if error_rate > self.alert_threshold.error_rate:
                    self._trigger_alert(
                        level="error",
                        type="high_error_rate",
                        message=f"错误率过高: {error_rate:.1%} > "
                               f"{self.alert_threshold.error_rate:.1%}",
                        function_name=metrics.function_name,
                        value=error_rate,
                        threshold=self.alert_threshold.error_rate
                    )

    def _trigger_alert(
        self,
        level: str,
        type: str,
        message: str,
        function_name: str,
        value: float,
        threshold: float
    ) -> None:
        """触发告警"""
        alert = {
            'id': f"{type}_{int(time.time()*1000)}",
            'timestamp': time.time(),
            'level': level,
            'type': type,
            'message': message,
            'function_name': function_name,
            'value': value,
            'threshold': threshold,
            'resolved': False
        }

        self._alerts.append(alert)

        # 记录日志
        if level == "error":
            self.logger.error(f"JIT编译告警: {message}")
        else:
            self.logger.warning(f"JIT编译告警: {message}")

        # 调用告警回调
        for callback in self._alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                self.logger.error(f"告警回调失败: {e}")

    def register_alert_callback(self, callback: Callable) -> None:
        """注册告警回调函数"""
        self._alert_callbacks.append(callback)

    def get_performance_stats(self, function_name: str) -> Optional[PerformanceStats]:
        """
        获取函数性能统计

        Args:
            function_name: 函数名称

        Returns:
            性能统计信息，如果函数不存在则返回None
        """
        with self._lock:
            return self._function_stats.get(function_name)

    def get_all_stats(self) -> Dict[str, PerformanceStats]:
        """获取所有函数统计信息"""
        with self._lock:
            return self._function_stats.copy()

    def get_recent_metrics(
        self,
        function_name: str,
        n: int = 10
    ) -> List[CompileMetrics]:
        """
        获取最近编译指标

        Args:
            function_name: 函数名称
            n: 返回的最近指标数量

        Returns:
            最近编译指标列表
        """
        with self._lock:
            deque_data = self._recent_metrics.get(function_name, deque())
            return list(deque_data)[-n:]

    def get_performance_report(self) -> Dict[str, Any]:
        """
        获取性能报告

        Returns:
            性能报告字典
        """
        with self._lock:
            # 总体统计
            total_compilations = sum(
                s.total_compilations for s in self._function_stats.values()
            )
            total_cache_hits = sum(s.cache_hits for s in self._function_stats.values())
            total_cache_misses = sum(
                s.cache_misses for s in self._function_stats.values()
            )

            cache_hit_rate = (
                total_cache_hits / (total_cache_hits + total_cache_misses)
                if (total_cache_hits + total_cache_misses) > 0
                else 0.0
            )

            # 函数性能排名
            function_performance = []
            for func_name, stats in self._function_stats.items():
                if stats.total_compilations > 0:
                    function_performance.append({
                        'function_name': func_name,
                        'avg_compile_time_ms': stats.avg_compile_time * 1000,
                        'total_compilations': stats.total_compilations,
                        'cache_hit_rate': (
                            stats.cache_hits /
                            (stats.cache_hits + stats.cache_misses)
                            if (stats.cache_hits + stats.cache_misses) > 0
                            else 0.0
                        ),
                        'performance_level': stats.performance_level.value
                    })

            # 按平均编译时间排序
            function_performance.sort(key=lambda x: x['avg_compile_time_ms'])

            # 告警统计
            active_alerts = [a for a in self._alerts if not a['resolved']]
            recent_alerts = sorted(
                self._alerts[-10:],
                key=lambda x: x['timestamp'],
                reverse=True
            )

            return {
                'timestamp': time.time(),
                'monitoring_active': self._is_monitoring,
                'total_functions_monitored': len(self._function_stats),
                'total_compilations': total_compilations,
                'cache_hit_rate': cache_hit_rate,
                'active_alerts': len(active_alerts),
                'function_performance': function_performance,
                'recent_alerts': recent_alerts[:5],
                'performance_summary': {
                    'excellent': sum(1 for f in function_performance
                                   if f['performance_level'] == 'excellent'),
                    'good': sum(1 for f in function_performance
                               if f['performance_level'] == 'good'),
                    'acceptable': sum(1 for f in function_performance
                                     if f['performance_level'] == 'acceptable'),
                    'slow': sum(1 for f in function_performance
                               if f['performance_level'] == 'slow'),
                    'critical': sum(1 for f in function_performance
                                   if f['performance_level'] == 'critical')
                }
            }

    def get_trend_analysis(
        self,
        function_name: str,
        window_size: int = 100
    ) -> Dict[str, Any]:
        """
        获取性能趋势分析

        Args:
            function_name: 函数名称
            window_size: 分析窗口大小

        Returns:
            趋势分析结果
        """
        with self._lock:
            if function_name not in self._function_stats:
                return {'error': 'Function not found'}

            # 获取历史数据
            history = self._compile_history[-window_size:]
            function_history = [
                m for m in history if m.function_name == function_name
            ]

            if len(function_history) < 2:
                return {'error': 'Insufficient data for trend analysis'}

            # 提取时间序列数据
            timestamps = [m.timestamp for m in function_history]
            compile_times = [m.compile_time * 1000 for m in function_history]  # ms

            # 计算简单趋势（线性回归斜率）
            n = len(compile_times)
            if n >= 2:
                x = list(range(n))
                sum_x = sum(x)
                sum_y = sum(compile_times)
                sum_xy = sum(x[i] * compile_times[i] for i in range(n))
                sum_x2 = sum(xi * xi for xi in x)

                if n * sum_x2 - sum_x * sum_x != 0:
                    slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x * sum_x)
                else:
                    slope = 0
            else:
                slope = 0

            # 评估趋势
            if slope > 0.5:
                trend = 'degrading'
                trend_strength = 'strong'
            elif slope > 0.1:
                trend = 'degrading'
                trend_strength = 'moderate'
            elif slope > -0.1:
                trend = 'stable'
                trend_strength = 'weak'
            elif slope > -0.5:
                trend = 'improving'
                trend_strength = 'moderate'
            else:
                trend = 'improving'
                trend_strength = 'strong'

            # 计算性能指标
            avg_compile_time = sum(compile_times) / n
            min_time = min(compile_times)
            max_time = max(compile_times)

            # 计算变异系数（稳定性指标）
            if avg_compile_time > 0:
                cv = statistics.stdev(compile_times) / avg_compile_time
            else:
                cv = 0

            return {
                'function_name': function_name,
                'sample_count': n,
                'time_period_days': (max(timestamps) - min(timestamps)) / 86400,
                'avg_compile_time_ms': avg_compile_time,
                'min_compile_time_ms': min_time,
                'max_compile_time_ms': max_time,
                'coefficient_of_variation': cv,
                'trend': trend,
                'trend_strength': trend_strength,
                'trend_slope_ms_per_event': slope,
                'recommendation': self._generate_trend_recommendation(
                    trend, slope, cv, avg_compile_time
                )
            }

    def _generate_trend_recommendation(
        self,
        trend: str,
        slope: float,
        cv: float,
        avg_time: float
    ) -> str:
        """生成趋势推荐"""
        if trend == 'degrading' and slope > 0.3:
            return "性能显著下降，建议检查函数复杂度和缓存配置"
        elif trend == 'degrading':
            return "性能轻微下降，建议监控"
        elif cv > 0.5:
            return "编译时间波动较大，建议检查输入数据一致性"
        elif avg_time > 200:
            return "编译时间过长，建议优化函数逻辑或增加缓存"
        elif avg_time > 50:
            return "编译时间适中，可继续监控"
        else:
            return "性能优秀，保持当前配置"

    def clear_history(self) -> None:
        """清除历史记录"""
        with self._lock:
            self._compile_history.clear()
            self._function_stats.clear()
            self._recent_metrics.clear()
            self._alerts.clear()
            self.logger.info("JIT监控历史记录已清除")

    def shutdown(self) -> None:
        """关闭监控器"""
        if self._is_shutdown:
            return

        with self._lock:
            # 停止监控
            if self._is_monitoring:
                self.stop_monitoring()

            # 生成最终报告
            report = self.get_performance_report()

            self.logger.info(f"JIT监控器关闭，总编译次数: {report['total_compilations']}")
            self.logger.info(f"缓存命中率: {report['cache_hit_rate']:.1%}")

            self._is_shutdown = True


# 装饰器工具函数
def track_function(
    monitor: Optional[JITMonitor] = None,
    critical: bool = False,
    enable_phase_tracking: bool = False
) -> Callable:
    """
    跟踪函数编译性能的装饰器

    Args:
        monitor: JITMonitor实例，None则使用默认
        critical: 是否为关键函数
        enable_phase_tracking: 是否启用阶段跟踪

    Returns:
        装饰器函数
    """
    # 获取或创建默认监控器
    if monitor is None:
        from .jit_monitor import get_default_monitor
        monitor = get_default_monitor()

    def decorator(func):
        # 保存原始函数
        original_func = func

        # 创建包装函数
        def wrapper(*args, **kwargs):
            # 记录编译开始时间
            compile_start = time.perf_counter()

            try:
                # 执行函数
                result = original_func(*args, **kwargs)

                # 记录编译时间
                compile_time = time.perf_counter() - compile_start

                # 记录编译事件
                monitor.record_compile_event(
                    function_name=func.__name__,
                    compile_time=compile_time,
                    cache_hit=False,  # 假设首次执行都是未命中
                    cache_source=None
                )

                return result

            except Exception as e:
                # 记录错误
                compile_time = time.perf_counter() - compile_start
                monitor.record_compile_event(
                    function_name=func.__name__,
                    compile_time=compile_time,
                    success=False,
                    error_message=str(e)
                )
                raise

        # 复制原始函数的属性
        wrapper.__name__ = original_func.__name__
        wrapper.__doc__ = original_func.__doc__
        wrapper.__module__ = original_func.__module__

        return wrapper

    return decorator


# 默认全局监控器实例
_default_monitor: Optional[JITMonitor] = None


def get_default_monitor() -> JITMonitor:
    """获取默认监控器"""
    global _default_monitor
    if _default_monitor is None:
        _default_monitor = JITMonitor(
            enable_phase_tracking=False,
            alert_threshold=AlertThreshold(
                compile_time_ms=500.0,
                memory_mb=100.0,
                error_rate=0.1,
                consecutive_failures=3,
                cache_miss_rate=0.5
            )
        )
        _default_monitor.start_monitoring()
    return _default_monitor


def get_performance_summary() -> Dict[str, Any]:
    """获取性能摘要（使用默认监控器）"""
    monitor = get_default_monitor()
    return monitor.get_performance_report()


def analyze_function_trend(
    function_name: str,
    window_size: int = 100
) -> Dict[str, Any]:
    """分析函数性能趋势（使用默认监控器）"""
    monitor = get_default_monitor()
    return monitor.get_trend_analysis(function_name, window_size)


# 上下文管理器支持
class MonitorContext:
    """监控器上下文管理器"""

    def __init__(
        self,
        enable_phase_tracking: bool = False,
        alert_threshold: Optional[AlertThreshold] = None
    ):
        self.enable_phase_tracking = enable_phase_tracking
        self.alert_threshold = alert_threshold
        self.monitor: Optional[JITMonitor] = None

    def __enter__(self) -> JITMonitor:
        self.monitor = JITMonitor(
            enable_phase_tracking=self.enable_phase_tracking,
            alert_threshold=self.alert_threshold
        )
        self.monitor.start_monitoring()
        return self.monitor

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.monitor:
            self.monitor.shutdown()