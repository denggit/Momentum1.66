#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/13/26 11:56 PM
@File       : __init__.py
@Description: 四号引擎(TripleA)主包 - 重新导出所有子模块以保持向后兼容性
"""

# 重新导出核心模块
from .core.data_structures import (
    NormalizedTick,
    TripleAEngineConfig,
    KDEEngineConfig,
    RangeBarConfig,
    RiskManagerConfig,
    PositionState
)

# 从KDE模块重新导出
from .kde.lvn_extractor import LVNRegion

# 重新导出数据处理模块
from .data_processing.range_bar_generator import RangeBarGenerator
from .data_processing.cvd_calculator import CVDCalculator

# 重新导出KDE模块
from .kde.kde_engine import KDEEngine
from .kde.kde_core import KDECore
from .kde.kde_matrix import KDEMatrixEngine
from .kde.lvn_extractor import LVNExtractor
from .kde.matrix_ops import (
    broadcast_subtract,
    broadcast_gaussian_kernel,
    compute_density_grid
)

# 重新导出LVN模块
from .lvn.lvn_manager import LVNManager

# 重新导出状态机模块
from .state_machine.state_machine import (
    TripleAStateMachine,
    TripleAState,
    StateTransitionEvent,
    StateContext
)

# 重新导出风险管理模块
from .risk.risk_manager import RiskManager
from .risk.real_time_risk_monitor import RealTimeRiskMonitor, RiskAlert, RiskLevel
from .risk.position_guard import PositionGuard

# 重新导出信号生成模块
from .signal.signal_generator import TripleASignalGenerator
from .signal.research_generator import ResearchGenerator

# 重新导出订单执行模块
from .execution.okx_executor import (
    OKXOrderExecutor,
    OKXAPIConfig,
    OrderRequest,
    OrderType,
    OrderStatus
)
from .execution.order_manager import OrderManager

# 重新导出性能优化模块
from .optimization.cpu_affinity import CPUAffinityManager
from .optimization.jit_monitor import JITMonitor
from .optimization.numba_cache import NumbaCacheManager
from .optimization.numba_warmup import NumbaWarmupManager
from .optimization.process_pool_manager import ProcessPoolManager
from .optimization.serialization import (
    encode_numpy_array,
    decode_numpy_array,
    compress_data,
    decompress_data
)

# 重新导出系统工具模块
from .system.connection_health import ConnectionHealthMonitor, HealthMonitor
from .system.emergency_handler import EmergencyHandler
from .system.ipc_protocol import IPCProtocol

# 版本信息
__version__ = "3.0.0"
__author__ = "Zijun Deng"
__description__ = "四号引擎(TripleA) - 实时量化交易系统"