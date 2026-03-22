"""
四号引擎v3.0 CPU亲和性管理器
精准CPU核心绑定，支持双核隔离架构
"""

import os
import platform
import threading
from enum import Enum
from typing import List, Optional, Dict, Any

import psutil

# 导入现有日志模块
from src.utils.log import get_logger


class CPUAffinityError(Exception):
    """CPU亲和性错误"""
    pass


class PlatformSupport(Enum):
    """平台支持级别"""
    FULL = "full"  # 完全支持
    PARTIAL = "partial"  # 部分支持
    NONE = "none"  # 不支持


class CPUAffinityManager:
    """CPU亲和性管理器"""

    def __init__(self, logger: Optional[Any] = None):
        """
        初始化CPU亲和性管理器

        Args:
            logger: 日志记录器
        """
        self.logger = logger or get_logger(__name__)
        self.platform_info = self._detect_platform()
        self.process = psutil.Process(os.getpid())

        # CPU信息
        self.cpu_count_physical = psutil.cpu_count(logical=False)
        self.cpu_count_logical = psutil.cpu_count(logical=True)

        # 亲和性状态
        self.original_affinity: Optional[List[int]] = None
        self.current_affinity: Optional[List[int]] = None
        self.managed_processes: Dict[int, List[int]] = {}  # PID -> 亲和性设置

        self.logger.info(f"🔧 CPU亲和性管理器初始化: "
                         f"物理核心: {self.cpu_count_physical}, "
                         f"逻辑核心: {self.cpu_count_logical}, "
                         f"平台: {self.platform_info}")

    def _detect_platform(self) -> Dict[str, str]:
        """检测平台信息"""
        system = platform.system().lower()
        release = platform.release()

        info = {
            'system': system,
            'release': release,
            'machine': platform.machine(),
            'processor': platform.processor(),
            'support_level': PlatformSupport.NONE.value
        }

        # 检测支持级别
        if system in ['linux', 'darwin']:
            # Linux和macOS通常支持sched_setaffinity
            info['support_level'] = PlatformSupport.FULL.value

            # 检查是否在容器中运行
            if system == 'linux' and os.path.exists('/.dockerenv'):
                info['containerized'] = True
                self.logger.warning("⚠️  检测到在Docker容器中运行，CPU亲和性可能受限")

        elif system == 'windows':
            # Windows支持SetProcessAffinityMask
            info['support_level'] = PlatformSupport.PARTIAL.value
        else:
            self.logger.warning(f"⚠️  未知操作系统: {system}")

        return info

    def get_cpu_topology(self) -> Dict[str, any]:
        """获取CPU拓扑信息"""
        topology = {
            'physical_cores': self.cpu_count_physical,
            'logical_cores': self.cpu_count_logical,
            'hyperthreading': self.cpu_count_logical > self.cpu_count_physical,
            'cores': []
        }

        # 尝试获取更详细的拓扑信息
        try:
            # Linux特定：从/proc/cpuinfo获取信息
            if self.platform_info['system'] == 'linux':
                topology.update(self._get_linux_cpu_topology())
            # macOS特定
            elif self.platform_info['system'] == 'darwin':
                topology.update(self._get_macos_cpu_topology())
            # Windows特定
            elif self.platform_info['system'] == 'windows':
                topology.update(self._get_windows_cpu_topology())
        except Exception as e:
            self.logger.warning(f"无法获取详细CPU拓扑: {e}")

        return topology

    def _get_linux_cpu_topology(self) -> Dict[str, any]:
        """获取Linux CPU拓扑信息"""
        topology = {
            'sockets': 1,
            'cores_per_socket': self.cpu_count_physical,
            'threads_per_core': self.cpu_count_logical // self.cpu_count_physical
            if self.cpu_count_physical > 0 else 1
        }

        try:
            # 尝试从/proc/cpuinfo获取更多信息
            if os.path.exists('/proc/cpuinfo'):
                with open('/proc/cpuinfo', 'r') as f:
                    cpuinfo = f.read()

                # 解析物理ID和核心ID
                physical_ids = set()
                core_ids = set()

                for line in cpuinfo.split('\n'):
                    if 'physical id' in line:
                        physical_ids.add(int(line.split(':')[1].strip()))
                    elif 'core id' in line:
                        core_ids.add(int(line.split(':')[1].strip()))

                if physical_ids:
                    topology['sockets'] = len(physical_ids)
                if core_ids:
                    topology['cores_per_socket'] = len(core_ids) // len(physical_ids) \
                        if physical_ids else len(core_ids)

        except Exception as e:
            self.logger.debug(f"解析/proc/cpuinfo失败: {e}")

        return topology

    def _get_macos_cpu_topology(self) -> Dict[str, any]:
        """获取macOS CPU拓扑信息"""
        # macOS使用sysctl获取信息
        import subprocess

        topology = {
            'sockets': 1,
            'cores_per_socket': self.cpu_count_physical
        }

        try:
            # 获取物理核心数
            result = subprocess.run(['sysctl', '-n', 'hw.physicalcpu'],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                topology['cores_per_socket'] = int(result.stdout.strip())

            # 获取CPU包数（socket）
            result = subprocess.run(['sysctl', '-n', 'hw.packages'],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                topology['sockets'] = int(result.stdout.strip())

        except Exception as e:
            self.logger.debug(f"获取macOS CPU信息失败: {e}")

        return topology

    def _get_windows_cpu_topology(self) -> Dict[str, any]:
        """获取Windows CPU拓扑信息"""
        # Windows使用WMI获取信息（简化版本）
        topology = {
            'sockets': 1,
            'cores_per_socket': self.cpu_count_physical
        }

        try:
            import ctypes.wintypes

            # 使用GetLogicalProcessorInformation API
            class SYSTEM_LOGICAL_PROCESSOR_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('ProcessorMask', ctypes.c_ulonglong),
                    ('Relationship', ctypes.c_ulong),
                    ('Reserved', ctypes.c_ulong * 2)
                ]

            # 简化实现，返回默认值
            # 在实际实现中，这里会调用Windows API

        except Exception as e:
            self.logger.debug(f"获取Windows CPU拓扑失败: {e}")

        return topology

    def save_original_affinity(self):
        """保存原始CPU亲和性设置"""
        try:
            self.original_affinity = self.process.cpu_affinity()
            self.logger.debug(f"💾 保存原始CPU亲和性: {self.original_affinity}")
        except Exception as e:
            self.logger.warning(f"保存原始CPU亲和性失败: {e}")
            self.original_affinity = list(range(self.cpu_count_logical))

    def restore_original_affinity(self):
        """恢复原始CPU亲和性设置"""
        if self.original_affinity is None:
            self.logger.warning("没有保存的原始CPU亲和性设置")
            return

        try:
            self.process.cpu_affinity(self.original_affinity)
            self.current_affinity = self.original_affinity
            self.logger.debug(f"🔄 恢复原始CPU亲和性: {self.original_affinity}")
        except Exception as e:
            self.logger.error(f"恢复原始CPU亲和性失败: {e}")

    def set_affinity(self, cores: List[int], pid: Optional[int] = None) -> bool:
        """
        设置CPU亲和性

        Args:
            cores: CPU核心列表（逻辑核心编号）
            pid: 进程ID，None表示当前进程

        Returns:
            是否成功
        """
        try:
            # 验证核心编号
            if not cores:
                raise CPUAffinityError("CPU核心列表不能为空")

            max_core = max(cores)
            if max_core >= self.cpu_count_logical:
                raise CPUAffinityError(
                    f"CPU核心编号 {max_core} 超出范围 (0-{self.cpu_count_logical - 1})"
                )

            # 获取目标进程
            if pid is None:
                target_process = self.process
            else:
                target_process = psutil.Process(pid)

            # 设置亲和性
            target_process.cpu_affinity(cores)

            # 记录状态
            if pid is None:
                self.current_affinity = cores
            else:
                self.managed_processes[pid] = cores

            self.logger.debug(f"✅ 设置CPU亲和性成功: PID={pid or os.getpid()}, 核心={cores}")
            return True

        except psutil.NoSuchProcess:
            self.logger.error(f"进程不存在: PID={pid}")
            return False
        except psutil.AccessDenied:
            self.logger.error(f"权限不足，无法设置进程 {pid} 的CPU亲和性")
            return False
        except Exception as e:
            self.logger.error(f"设置CPU亲和性失败: {e}")
            return False

    def set_affinity_for_triplea(self,
                                 main_process_core: int = 0,
                                 worker_process_core: int = 1) -> Dict[str, any]:
        """
        为四号引擎设置双核隔离亲和性

        Args:
            main_process_core: 主进程核心（I/O密集）
            worker_process_core: Worker进程核心（CPU密集）

        Returns:
            设置结果
        """
        result = {
            'main_process': {'success': False, 'core': main_process_core},
            'worker_process': {'success': False, 'core': worker_process_core},
            'recommendations': []
        }

        # 验证核心配置
        available_cores = list(range(self.cpu_count_logical))
        if main_process_core not in available_cores:
            result['recommendations'].append(
                f"主进程核心 {main_process_core} 无效，可用核心: {available_cores}"
            )
            return result

        if worker_process_core not in available_cores:
            result['recommendations'].append(
                f"Worker进程核心 {worker_process_core} 无效，可用核心: {available_cores}"
            )
            return result

        if main_process_core == worker_process_core:
            result['recommendations'].append(
                "⚠️  主进程和Worker进程使用相同核心，无法实现完全隔离"
            )

        # 设置主进程亲和性
        main_success = self.set_affinity([main_process_core])
        result['main_process']['success'] = main_success

        # 保存Worker进程核心配置（实际在Worker进程中设置）
        result['worker_process']['core'] = worker_process_core
        result['worker_process']['success'] = True  # 标记为配置成功

        if main_success:
            self.logger.info(
                f"✅ 四号引擎CPU亲和性配置完成: "
                f"主进程→核心{main_process_core}, "
                f"Worker进程→核心{worker_process_core}"
            )

            # 生成推荐配置
            self._generate_recommendations(result)

        return result

    def _generate_recommendations(self, result: Dict[str, any]):
        """生成推荐配置"""
        topology = self.get_cpu_topology()

        if topology.get('hyperthreading', False):
            result['recommendations'].append(
                "💡 系统启用了超线程，建议将主进程和Worker进程分配到不同的物理核心"
            )

        if topology.get('sockets', 1) > 1:
            result['recommendations'].append(
                f"💡 系统有 {topology['sockets']} 个CPU插槽，建议将相关进程分配到同一插槽以减少跨插槽延迟"
            )

        # 性能监控建议
        result['recommendations'].extend([
            "📊 建议监控每个核心的利用率，确保负载均衡",
            "⚡ 如果Worker进程CPU使用率持续>90%，考虑减少任务负载",
            "🔄 定期检查CPU亲和性设置，防止被系统或管理员更改"
        ])

    def get_affinity(self, pid: Optional[int] = None) -> Optional[List[int]]:
        """
        获取CPU亲和性设置

        Args:
            pid: 进程ID，None表示当前进程

        Returns:
            CPU核心列表，失败返回None
        """
        try:
            if pid is None:
                return self.process.cpu_affinity()
            else:
                return psutil.Process(pid).cpu_affinity()
        except Exception as e:
            self.logger.error(f"获取CPU亲和性失败: {e}")
            return None

    def get_core_utilization(self, interval: float = 1.0) -> Dict[int, float]:
        """
        获取每个CPU核心的利用率

        Args:
            interval: 采样间隔（秒）

        Returns:
            核心编号 -> 利用率百分比
        """
        try:
            # 获取初始CPU时间
            cpu_times_start = psutil.cpu_times_percent(interval=0)

            # 等待采样间隔
            import time
            time.sleep(interval)

            # 获取结束CPU时间
            cpu_percent = psutil.cpu_percent(interval=0, percpu=True)

            utilization = {}
            for core, percent in enumerate(cpu_percent):
                utilization[core] = percent

            return utilization

        except Exception as e:
            self.logger.error(f"获取CPU利用率失败: {e}")
            return {}

    def monitor_affinity_compliance(self,
                                    expected_affinity: Dict[int, List[int]],
                                    check_interval: float = 5.0) -> threading.Thread:
        """
        监控CPU亲和性合规性

        Args:
            expected_affinity: 预期亲和性设置 {PID: 核心列表}
            check_interval: 检查间隔（秒）

        Returns:
            监控线程
        """

        def monitor():
            while getattr(threading.current_thread(), "do_run", True):
                try:
                    for pid, expected_cores in expected_affinity.items():
                        actual_cores = self.get_affinity(pid)

                        if actual_cores != expected_cores:
                            self.logger.warning(
                                f"⚠️  CPU亲和性违规: PID={pid}, "
                                f"期望={expected_cores}, 实际={actual_cores}"
                            )

                            # 自动修复
                            try:
                                if psutil.pid_exists(pid):
                                    self.set_affinity(expected_cores, pid)
                                    self.logger.info(f"✅ 自动修复PID {pid} 的CPU亲和性")
                            except:
                                pass

                    time.sleep(check_interval)

                except Exception as e:
                    self.logger.error(f"监控CPU亲和性失败: {e}")
                    time.sleep(check_interval)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.do_run = True
        thread.start()

        return thread

    def verify_dual_core_isolation(self,
                                   main_pid: int,
                                   worker_pid: int) -> Dict[str, any]:
        """
        验证双核隔离配置

        Args:
            main_pid: 主进程ID
            worker_pid: Worker进程ID

        Returns:
            验证结果
        """
        result = {
            'main_process': {'pid': main_pid, 'cores': None, 'isolation': False},
            'worker_process': {'pid': worker_pid, 'cores': None, 'isolation': False},
            'cross_core_isolation': False,
            'recommendations': []
        }

        try:
            # 获取主进程亲和性
            main_cores = self.get_affinity(main_pid)
            result['main_process']['cores'] = main_cores

            # 获取Worker进程亲和性
            worker_cores = self.get_affinity(worker_pid)
            result['worker_process']['cores'] = worker_cores

            if main_cores and worker_cores:
                # 检查进程内隔离（单个进程是否绑定到单个核心）
                result['main_process']['isolation'] = len(main_cores) == 1
                result['worker_process']['isolation'] = len(worker_cores) == 1

                # 检查跨进程隔离（是否使用不同核心）
                main_set = set(main_cores)
                worker_set = set(worker_cores)
                result['cross_core_isolation'] = len(main_set.intersection(worker_set)) == 0

                # 生成建议
                if not result['main_process']['isolation']:
                    result['recommendations'].append(
                        f"主进程绑定到 {len(main_cores)} 个核心，建议绑定到单个核心以实现更好隔离"
                    )

                if not result['worker_process']['isolation']:
                    result['recommendations'].append(
                        f"Worker进程绑定到 {len(worker_cores)} 个核心，建议绑定到单个核心以实现更好隔离"
                    )

                if not result['cross_core_isolation']:
                    result['recommendations'].append(
                        f"主进程和Worker进程共享核心 {main_set.intersection(worker_set)}，"
                        "建议使用不同核心以实现完全隔离"
                    )

                if result['cross_core_isolation'] and result['main_process']['isolation'] and result['worker_process'][
                    'isolation']:
                    result['recommendations'].append(
                        "✅ 双核隔离配置正确，主进程和Worker进程完全隔离"
                    )

        except Exception as e:
            result['error'] = str(e)
            self.logger.error(f"验证双核隔离失败: {e}")

        return result

    def get_recommended_configuration(self) -> Dict[str, any]:
        """获取推荐的四号引擎v3.0 CPU配置"""
        topology = self.get_cpu_topology()
        physical_cores = topology['physical_cores']

        config = {
            'server_type': '阿里云2C2G东京',
            'physical_cores': physical_cores,
            'logical_cores': self.cpu_count_logical,
            'recommended_cores': {},
            'performance_considerations': [],
            'warnings': []
        }

        if physical_cores >= 2:
            # 2C2G服务器推荐配置
            config['recommended_cores'] = {
                'main_process': 0,  # 核心0：主进程（I/O密集）
                'worker_process': 1  # 核心1：Worker进程（CPU密集）
            }

            config['performance_considerations'].extend([
                "✅ 物理核心充足，适合双核隔离架构",
                "⚡ 核心0应处理WebSocket接收、Tick预处理、状态机调度等I/O密集型任务",
                "🧮 核心1应处理KDE计算、CVD统计、矩阵运算等CPU密集型任务",
                "📊 监控核心利用率，确保负载均衡"
            ])

        elif physical_cores == 1:
            # 单核服务器配置
            config['recommended_cores'] = {
                'main_process': 0,
                'worker_process': 0  # 共享同一核心
            }

            config['warnings'].extend([
                "⚠️  只有1个物理核心，无法实现真正的双核隔离",
                "⚠️  I/O密集和CPU密集任务将竞争同一核心，可能影响性能",
                "💡 考虑升级到至少2物理核心的服务器"
            ])

            config['performance_considerations'].extend([
                "🔧 启用Numba JIT编译减少计算开销",
                "🔄 优化任务调度，避免同时执行多个CPU密集型任务",
                "📉 降低Tick处理频率以减少CPU负载"
            ])

        else:
            config['warnings'].append("❌ 无法检测到物理核心，请检查系统配置")

        # 超线程相关建议
        if topology.get('hyperthreading', False):
            threads_per_core = topology.get('threads_per_core', 2)

            config['performance_considerations'].append(
                f"💡 系统启用了超线程 ({threads_per_core}线程/核心)，"
                "建议将相关任务分配到同一物理核心的不同逻辑核心以减少上下文切换"
            )

        # 多插槽相关建议
        if topology.get('sockets', 1) > 1:
            config['performance_considerations'].append(
                f"💡 系统有 {topology['sockets']} 个CPU插槽，"
                "建议将相关进程分配到同一插槽以减少跨插槽内存访问延迟"
            )

        return config


# 全局CPU亲和性管理器实例
_default_manager: Optional[CPUAffinityManager] = None


def get_default_manager() -> CPUAffinityManager:
    """获取默认CPU亲和性管理器"""
    global _default_manager
    if _default_manager is None:
        _default_manager = CPUAffinityManager()
    return _default_manager


# 导入time模块（需要在类定义后添加）
import time
