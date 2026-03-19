"""
四号引擎v3.0 ProcessPoolExecutor管理器
高性能进程池管理器，支持双核隔离架构
"""

import asyncio
import multiprocessing
import os
import queue
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, TypeVar

import psutil

# 导入现有日志模块
from src.utils.log import get_logger

# 导入配置加载器
from config.triplea import load_triplea_config

# 类型变量
T = TypeVar('T')
R = TypeVar('R')


class WorkerStatus(Enum):
    """Worker状态枚举"""
    IDLE = "idle"  # 空闲
    BUSY = "busy"  # 忙碌
    ERROR = "error"  # 错误
    STOPPED = "stopped"  # 停止
    INITIALIZING = "initializing"  # 初始化


@dataclass
class WorkerInfo:
    """Worker信息"""
    worker_id: int
    process_id: int
    status: WorkerStatus
    cpu_core: int = 0
    task_count: int = 0
    error_count: int = 0
    last_activity: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)


@dataclass
class TaskInfo:
    """任务信息"""
    task_id: str
    task_type: str
    data: Any
    priority: int = 0
    submitted_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    timeout_seconds: float = 30.0
    retry_count: int = 0
    max_retries: int = 3

    def __lt__(self, other):
        """比较操作，用于优先级队列（相同优先级的任务按提交时间排序）"""
        # 优先级队列已经比较了优先级，这里比较其他字段
        if self.submitted_at != other.submitted_at:
            return self.submitted_at < other.submitted_at
        # 如果提交时间相同，按任务ID比较
        return self.task_id < other.task_id


class ProcessPoolManager:
    """高性能进程池管理器"""

    def __init__(self,
                 max_workers: Optional[int] = None,
                 cpu_affinity: Optional[List[int]] = None,
                 task_queue_size: Optional[int] = None,
                 enable_heartbeat: Optional[bool] = None,
                 heartbeat_interval: Optional[float] = None,
                 worker_timeout: Optional[float] = None):
        """
        初始化进程池管理器

        Args:
            max_workers: 最大Worker数量，None则从配置读取
            cpu_affinity: CPU亲和性设置（核心列表），None则从配置读取
            task_queue_size: 任务队列大小，None则从配置读取
            enable_heartbeat: 是否启用心跳检测，None则从配置读取
            heartbeat_interval: 心跳检测间隔（秒），None则从配置读取
            worker_timeout: Worker超时时间（秒），None则从配置读取
        """
        # 加载配置
        config = load_triplea_config(config_type="engine")
        process_pool_config = config.get("process_pool", {})

        # 使用参数值或配置值，如果都没有则使用硬编码默认值
        self.max_workers = max_workers or process_pool_config.get("max_workers", 1)
        self.cpu_affinity = cpu_affinity or []
        self.task_queue_size = task_queue_size or process_pool_config.get("task_queue_size", 1000)
        self.enable_heartbeat = enable_heartbeat if enable_heartbeat is not None else process_pool_config.get("enable_heartbeat", True)
        self.heartbeat_interval = heartbeat_interval or process_pool_config.get("heartbeat_interval", 5.0)
        self.worker_timeout = worker_timeout or process_pool_config.get("worker_timeout", 60.0)

        # 进程池和任务管理
        self.executor: Optional[ProcessPoolExecutor] = None
        self.task_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=task_queue_size)
        self.pending_tasks: Dict[str, TaskInfo] = {}
        self.completed_tasks: Dict[str, TaskInfo] = {}
        self.worker_infos: Dict[int, WorkerInfo] = {}

        # 异步管理
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._worker_counter = 0
        self._task_counter = 0

        # 同步锁
        self._lock = threading.RLock()
        self._task_lock = threading.RLock()

        # 统计信息
        self.stats = {
            'tasks_submitted': 0,
            'tasks_completed': 0,
            'tasks_failed': 0,
            'tasks_timeout': 0,
            'total_processing_time': 0.0,
            'avg_processing_time': 0.0,
            'peak_queue_size': 0,
            'worker_restarts': 0,
            'start_time': time.time()  # 添加启动时间
        }

        # 日志
        self.logger = get_logger(__name__)

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """获取事件循环（延迟初始化）"""
        if self._loop is None:
            try:
                # 尝试获取当前运行的事件循环
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                # 如果没有运行的事件循环，获取或创建事件循环
                self._loop = asyncio.get_event_loop()
        return self._loop

    async def start(self):
        """启动进程池管理器"""
        if self._running:
            self.logger.warning("进程池管理器已经在运行")
            return

        self.logger.info("🚀 启动ProcessPoolExecutor管理器...")
        self._running = True

        # 创建进程池
        self.executor = ProcessPoolExecutor(
            max_workers=self.max_workers,
            mp_context=multiprocessing.get_context('spawn')  # 使用spawn上下文避免fork问题
        )

        # 初始化Worker信息
        await self._initialize_workers()

        # 启动任务分发器
        self._tasks.append(
            asyncio.create_task(self._task_dispatcher())
        )

        # 启动心跳检测（如果启用）
        if self.enable_heartbeat:
            self._tasks.append(
                asyncio.create_task(self._heartbeat_monitor())
            )

        # 启动统计收集器
        self._tasks.append(
            asyncio.create_task(self._stats_collector())
        )

        self.logger.info(f"✅ ProcessPoolExecutor管理器已启动，Worker数量: {self.max_workers}")

    async def stop(self):
        """停止进程池管理器"""
        if not self._running:
            return

        self.logger.info("🛑 停止ProcessPoolExecutor管理器...")
        self._running = False

        # 取消所有任务
        for task in self._tasks:
            task.cancel()

        # 等待任务完成
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except:
            pass

        # 关闭进程池
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)

        # 清空数据
        self.pending_tasks.clear()
        self.completed_tasks.clear()
        self.worker_infos.clear()

        self.logger.info("✅ ProcessPoolExecutor管理器已停止")

    async def submit_task(self,
                          task_type: str,
                          data: Any,
                          priority: int = 0,
                          timeout_seconds: Optional[float] = None,
                          max_retries: Optional[int] = None) -> str:
        """
        提交任务

        Args:
            task_type: 任务类型
            data: 任务数据
            priority: 任务优先级（数字越小优先级越高）
            timeout_seconds: 任务超时时间，None则从配置读取
            max_retries: 最大重试次数，None则从配置读取

        Returns:
            任务ID
        """
        with self._task_lock:
            # 加载配置获取默认值
            config = load_triplea_config(config_type="engine")
            process_pool_config = config.get("process_pool", {})

            # 使用参数值或配置值
            actual_timeout = timeout_seconds or process_pool_config.get("task_timeout", 30.0)
            actual_max_retries = max_retries or process_pool_config.get("max_retries", 3)

            # 生成任务ID
            self._task_counter += 1
            task_id = f"task_{self._task_counter}_{int(time.time() * 1000)}"

            # 创建任务信息
            task_info = TaskInfo(
                task_id=task_id,
                task_type=task_type,
                data=data,
                priority=priority,
                timeout_seconds=actual_timeout,
                max_retries=actual_max_retries
            )

            # 添加到待处理队列
            try:
                self.task_queue.put((priority, task_info), block=False)
                self.pending_tasks[task_id] = task_info

                self.stats['tasks_submitted'] += 1

                # 更新峰值队列大小
                current_queue_size = self.task_queue.qsize()
                if current_queue_size > self.stats['peak_queue_size']:
                    self.stats['peak_queue_size'] = current_queue_size

                self.logger.debug(f"✅ 任务提交成功: {task_id} (类型: {task_type}, 优先级: {priority})")

                return task_id

            except queue.Full:
                self.logger.error(f"❌ 任务队列已满，无法提交任务: {task_id}")
                raise

    async def get_task_result(self, task_id: str, timeout: float = 5.0) -> Any:
        """
        获取任务结果

        Args:
            task_id: 任务ID
            timeout: 等待超时时间

        Returns:
            任务结果
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            # 检查已完成任务
            if task_id in self.completed_tasks:
                task_info = self.completed_tasks[task_id]
                if task_info.error:
                    raise Exception(f"任务执行失败: {task_info.error}")
                return task_info.result

            # 检查待处理任务
            if task_id in self.pending_tasks:
                task_info = self.pending_tasks[task_id]
                if task_info.started_at is None:
                    # 任务还未开始执行
                    await asyncio.sleep(0.01)
                    continue

                # 任务正在执行，等待完成
                await asyncio.sleep(0.01)
                continue

            # 任务不存在
            raise ValueError(f"任务不存在: {task_id}")

        raise TimeoutError(f"获取任务结果超时: {task_id}")

    async def _initialize_workers(self):
        """初始化Worker"""
        self.logger.debug("🔧 初始化Worker...")

        for worker_id in range(self.max_workers):
            # 获取Worker进程ID（实际在任务执行时获取）
            # 这里先创建Worker信息占位
            worker_info = WorkerInfo(
                worker_id=worker_id,
                process_id=0,  # 将在任务执行时设置
                status=WorkerStatus.INITIALIZING,
                cpu_core=self.cpu_affinity[worker_id % len(self.cpu_affinity)]
                if self.cpu_affinity else worker_id
            )

            self.worker_infos[worker_id] = worker_info

    async def _task_dispatcher(self):
        """任务分发器"""
        self.logger.debug("📤 启动任务分发器...")

        while self._running:
            try:
                # 从队列获取任务
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self._get_next_task
                )

                # 检查是否获取到任务
                if result is None:
                    await asyncio.sleep(0.01)
                    continue

                priority, task_info = result

                # 记录任务开始时间
                task_info.started_at = time.time()

                # 查找空闲Worker
                worker_id = await self._find_idle_worker()
                if worker_id is None:
                    # 没有空闲Worker，将任务放回队列
                    self.task_queue.put((priority, task_info))
                    await asyncio.sleep(0.01)
                    continue

                # 提交任务到进程池
                future = self.executor.submit(
                    ProcessPoolManager._worker_function,
                    worker_id,
                    task_info.task_type,
                    task_info.data,
                    task_info.task_id,
                    self.cpu_affinity
                )

                # 创建异步任务等待Future完成
                # 使用默认参数捕获当前值，避免闭包捕获变量引用
                async def wait_and_handle(worker_id=worker_id, task_info=task_info, future=future):
                    try:
                        # 将concurrent.futures.Future转换为asyncio.Future
                        asyncio_future = asyncio.wrap_future(future)
                        result = await asyncio_future  # 等待Future完成并获取结果
                        await self._handle_task_completion(result, task_info, worker_id)
                    except Exception as e:
                        # 如果Future抛出异常，创建错误结果
                        error_result = {
                            'task_id': task_info.task_id,
                            'error': str(e),
                            'worker_id': worker_id,
                            'process_id': os.getpid() if hasattr(os, 'getpid') else 0
                        }
                        await self._handle_task_completion(error_result, task_info, worker_id)

                asyncio.create_task(wait_and_handle())

                # 更新Worker状态
                with self._lock:
                    worker_info = self.worker_infos[worker_id]
                    worker_info.status = WorkerStatus.BUSY
                    worker_info.last_activity = time.time()

                # 短暂休眠以避免CPU过度使用
                await asyncio.sleep(0.001)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ 任务分发器异常: {e}")
                await asyncio.sleep(0.1)

    def _get_next_task(self):
        """获取下一个任务（线程安全）"""
        try:
            return self.task_queue.get_nowait()
        except queue.Empty:
            return None

    async def _find_idle_worker(self) -> Optional[int]:
        """查找空闲Worker"""
        with self._lock:
            for worker_id, worker_info in self.worker_infos.items():
                if worker_info.status in [WorkerStatus.IDLE, WorkerStatus.INITIALIZING]:
                    # 检查Worker是否超时
                    if time.time() - worker_info.last_activity > self.worker_timeout:
                        self.logger.warning(
                            f"⚠️ Worker {worker_id} 超时，重新启动"
                        )
                        await self._restart_worker(worker_id)
                        continue

                    return worker_id

        # 没有空闲Worker
        return None

    async def _restart_worker(self, worker_id: int):
        """重启Worker"""
        with self._lock:
            worker_info = self.worker_infos[worker_id]
            worker_info.status = WorkerStatus.ERROR
            worker_info.error_count += 1

        # 在实际实现中，这里会重启Worker进程
        # 简化版本：重置状态
        with self._lock:
            worker_info.status = WorkerStatus.IDLE
            worker_info.last_activity = time.time()

        self.stats['worker_restarts'] += 1

    @staticmethod
    def _worker_function(worker_id: int,
                         task_type: str,
                         data: Any,
                         task_id: str,
                         cpu_affinity: Optional[List[int]] = None) -> Any:
        """
        Worker进程函数

        Args:
            worker_id: Worker ID
            task_type: 任务类型
            data: 任务数据
            task_id: 任务ID

        Returns:
            任务结果
        """
        try:
            # 设置Worker进程的CPU亲和性
            if cpu_affinity:
                worker_pid = os.getpid()
                try:
                    worker_process = psutil.Process(worker_pid)
                    # 为Worker分配特定的CPU核心
                    core = cpu_affinity[worker_id % len(cpu_affinity)]
                    worker_process.cpu_affinity([core])
                except Exception as e:
                    # 亲和性设置失败，继续执行
                    pass

            # 根据任务类型执行不同的处理
            if task_type == "kde_computation":
                result = ProcessPoolManager._process_kde_task(data)
            elif task_type == "cvd_calculation":
                result = ProcessPoolManager._process_cvd_task(data)
            elif task_type == "rangebar_generation":
                result = ProcessPoolManager._process_rangebar_task(data)
            else:
                raise ValueError(f"未知的任务类型: {task_type}")

            # 更新Worker信息（通过共享内存或进程间通信）
            # 简化版本：返回结果

            return {
                'task_id': task_id,
                'result': result,
                'worker_id': worker_id,
                'process_id': os.getpid()
            }

        except Exception as e:
            # 捕获所有异常，确保Worker不会崩溃
            error_info = {
                'task_id': task_id,
                'error': str(e),
                'traceback': traceback.format_exc(),
                'worker_id': worker_id,
                'process_id': os.getpid()
            }

            return error_info

    async def _handle_task_completion(self,
                                      result: Dict[str, Any],
                                      task_info: TaskInfo,
                                      worker_id: int):
        """处理任务完成"""
        try:
            # 记录任务完成时间
            task_info.completed_at = time.time()

            # 处理结果
            if 'error' in result:
                task_info.error = result['error']

                # 检查是否需要重试
                if task_info.retry_count < task_info.max_retries:
                    task_info.retry_count += 1
                    self.logger.warning(
                        f"🔄 任务重试 {task_info.task_id} "
                        f"(第 {task_info.retry_count} 次)"
                    )

                    # 重新提交任务
                    await self.submit_task(
                        task_type=task_info.task_type,
                        data=task_info.data,
                        priority=task_info.priority,
                        timeout_seconds=task_info.timeout_seconds
                    )
                else:
                    # 重试次数用完，记录失败
                    self.stats['tasks_failed'] += 1

                    self.logger.error(
                        f"❌ 任务失败 {task_info.task_id}: {task_info.error}"
                    )

                    # 清理待处理任务
                    with self._task_lock:
                        if task_info.task_id in self.pending_tasks:
                            del self.pending_tasks[task_info.task_id]

                    # 添加到已完成任务
                    self.completed_tasks[task_info.task_id] = task_info

            else:
                # 任务成功
                task_info.result = result.get('result')

                # 更新统计信息
                processing_time = task_info.completed_at - (task_info.started_at or task_info.submitted_at)
                self.stats['tasks_completed'] += 1
                self.stats['total_processing_time'] += processing_time
                if self.stats['tasks_completed'] > 0:
                    self.stats['avg_processing_time'] = (
                        self.stats['total_processing_time'] / self.stats['tasks_completed']
                    )

                # 清理待处理任务
                with self._task_lock:
                    if task_info.task_id in self.pending_tasks:
                        del self.pending_tasks[task_info.task_id]

                # 添加到已完成任务
                self.completed_tasks[task_info.task_id] = task_info

                self.logger.debug(
                    f"✅ 任务完成 {task_info.task_id} "
                    f"(处理时间: {processing_time * 1000:.1f}ms)"
                )

        except Exception as e:
            # 其他异常
            task_info.error = f"处理结果异常: {e}"
            self.logger.error(
                f"❌ 处理任务结果异常 {task_info.task_id}: {e}"
            )

        finally:
            # 重置Worker状态（如果worker_id有效）
            if worker_id is not None:
                with self._lock:
                    if worker_id in self.worker_infos:
                        worker_info = self.worker_infos[worker_id]
                        worker_info.status = WorkerStatus.IDLE
                        worker_info.task_count += 1
                        worker_info.last_activity = time.time()
                    else:
                        self.logger.warning(f"⚠️ Worker ID {worker_id} 不存在于 worker_infos 中")
            else:
                self.logger.warning("⚠️ 任务完成时 worker_id 为 None")

    async def _heartbeat_monitor(self):
        """心跳监控器"""
        self.logger.debug("💓 启动心跳监控器...")

        while self._running:
            try:
                with self._lock:
                    current_time = time.time()

                    for worker_id, worker_info in self.worker_infos.items():
                        # 检查Worker是否超时

                        if (worker_info.status == WorkerStatus.BUSY and
                                current_time - worker_info.last_activity > self.worker_timeout):
                            self.logger.warning(
                                f"⚠️ Worker {worker_id} 心跳超时，状态: {worker_info.status}"
                            )

                            # 标记为错误状态

                            worker_info.status = WorkerStatus.ERROR

                # 休眠一段时间

                await asyncio.sleep(self.heartbeat_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ 心跳监控器异常: {e}")
                await asyncio.sleep(1.0)

    async def _stats_collector(self):
        """统计信息收集器"""
        self.logger.debug("📊 启动统计收集器...")

        while self._running:
            try:
                # 更新队列统计

                current_queue_size = self.task_queue.qsize()
                if current_queue_size > self.stats['peak_queue_size']:
                    self.stats['peak_queue_size'] = current_queue_size

                # 更新Worker统计

                with self._lock:
                    idle_count = sum(
                        1 for w in self.worker_infos.values()
                        if w.status == WorkerStatus.IDLE
                    )
                    busy_count = sum(
                        1 for w in self.worker_infos.values()
                        if w.status == WorkerStatus.BUSY
                    )
                    error_count = sum(
                        1 for w in self.worker_infos.values()
                        if w.status == WorkerStatus.ERROR
                    )

                # 记录统计信息（在实际实现中，可以发送到监控系统）

                if self.enable_heartbeat:
                    await asyncio.sleep(10.0)  # 每10秒收集一次

                else:
                    await asyncio.sleep(5.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ 统计收集器异常: {e}")
                await asyncio.sleep(5.0)

    @staticmethod
    def _process_kde_task(data: Dict) -> Dict:
        """处理KDE计算任务（简化版本）"""
        # 在实际实现中，这里会调用KDE计算引擎
        import numpy as np

        prices = np.array(data.get('prices', []))
        bandwidth = data.get('bandwidth', 0.5)

        # 简化KDE计算
        if len(prices) > 0:
            # 创建评估网格

            grid_points = np.linspace(
                prices.min(), prices.max(),
                min(100, len(prices))
            )

            # 简化KDE计算（实际实现会使用Numba优化）
            n = len(prices)
            # 使用广播进行向量化计算
            diff = prices[:, np.newaxis] - grid_points  # 形状 (n, m)
            kernel = np.exp(-0.5 * (diff / bandwidth) ** 2)  # 形状 (n, m)
            kde_values = np.sum(kernel, axis=0) / (n * bandwidth * np.sqrt(2 * np.pi))  # 形状 (m,)

            result = {
                'grid_points': grid_points.tolist(),
                'kde_values': kde_values.tolist(),
                'computation_time': 0.01  # 简化版本
            }
        else:
            result = {
                'grid_points': [],
                'kde_values': [],
                'computation_time': 0.0
            }

        return result

    @staticmethod
    def _process_cvd_task(data: Dict) -> Dict:
        """处理CVD计算任务（简化版本）"""
        # 在实际实现中，这里会调用CVD计算引擎
        trades = data.get('trades', [])

        cvd = 0.0
        buy_volume = 0.0
        sell_volume = 0.0

        for trade in trades:
            size = trade.get('size', 0.0)
            side = trade.get('side', '')

            if side == 'buy':
                cvd += size
                buy_volume += size
            elif side == 'sell':
                cvd -= size
                sell_volume += size

        result = {
            'cvd': cvd,
            'buy_volume': buy_volume,
            'sell_volume': sell_volume,
            'delta_ratio': (buy_volume - sell_volume) / (buy_volume + sell_volume) if (
                                                                                                  buy_volume + sell_volume) > 0 else 0.0
        }

        return result

    @staticmethod
    def _process_rangebar_task(data: Dict) -> Dict:
        """处理RangeBar生成任务（简化版本）"""
        # 在实际实现中，这里会调用RangeBar生成引擎

        ticks = data.get('ticks', [])
        bar_size = data.get('bar_size', 1.0)

        rangebars = []
        current_high = -float('inf')
        current_low = float('inf')
        current_volume = 0.0

        if ticks:
            open_price = ticks[0].get('price', 0.0)
            current_high = open_price
            current_low = open_price

            for tick in ticks:
                price = tick.get('price', 0.0)
                size = tick.get('size', 0.0)

                current_high = max(current_high, price)
                current_low = min(current_low, price)
                current_volume += size

                # 检查是否达到RangeBar大小

                if current_high - current_low >= bar_size:
                    close_price = price

                    rangebar = {
                        'open': open_price,
                        'high': current_high,
                        'low': current_low,
                        'close': close_price,
                        'volume': current_volume,
                        'tick_count': len(ticks)
                    }

                    rangebars.append(rangebar)

                    # 重置当前RangeBar

                    open_price = close_price
                    current_high = close_price
                    current_low = close_price
                    current_volume = 0.0

        result = {
            'rangebars': rangebars,
            'total_bars': len(rangebars),
            'remaining_ticks': max(0, len(ticks) - sum(r.get('tick_count', 0) for r in rangebars))
        }

        return result

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            stats = self.stats.copy()

            # 添加实时信息

            stats.update({
                'queue_size': self.task_queue.qsize(),
                'pending_tasks': len(self.pending_tasks),
                'completed_tasks': len(self.completed_tasks),
                'worker_count': len(self.worker_infos),
                'uptime': time.time() - self.stats.get('start_time', time.time()),
                'current_time': time.time()
            })

            return stats

    def get_worker_status(self) -> List[Dict[str, Any]]:
        """获取Worker状态"""
        with self._lock:
            return [
                {
                    'worker_id': info.worker_id,
                    'process_id': info.process_id,
                    'status': info.status.value,
                    'cpu_core': info.cpu_core,
                    'task_count': info.task_count,
                    'error_count': info.error_count,
                    'last_activity': info.last_activity,
                    'created_at': info.created_at
                }
                for info in self.worker_infos.values()
            ]


# 全局进程池管理器实例
_default_manager: Optional[ProcessPoolManager] = None


def get_default_manager() -> ProcessPoolManager:
    """获取默认进程池管理器"""
    global _default_manager
    if _default_manager is None:
        # 从配置加载CPU亲和性设置
        config = load_triplea_config(config_type="engine")
        cpu_affinity_config = config.get("cpu_affinity", {})

        worker_core = cpu_affinity_config.get("worker_process_core", 1)
        enable_affinity = cpu_affinity_config.get("enable_affinity", True)

        # 设置CPU亲和性
        cpu_affinity = [worker_core] if enable_affinity else None

        _default_manager = ProcessPoolManager(
            max_workers=None,  # None表示从配置读取
            cpu_affinity=cpu_affinity,
            task_queue_size=None,
            enable_heartbeat=None,
            heartbeat_interval=None,
            worker_timeout=None
        )
    return _default_manager
