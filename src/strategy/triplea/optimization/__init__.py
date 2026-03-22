#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/13/26 11:56 PM
@File       : __init__.py
@Description: 四号引擎性能优化模块 - CPU绑定、JIT编译、进程池等优化工具
"""

from .cpu_affinity import CPUAffinityManager
from .jit_monitor import JITMonitor
from .numba_cache import NumbaCacheManager
from .numba_warmup import NumbaWarmupManager
from .process_pool_manager import ProcessPoolManager
from .serialization import (
    encode_numpy_array,
    decode_numpy_array,
    compress_data,
    decompress_data
)

__all__ = [
    'CPUAffinityManager',
    'JITMonitor',
    'NumbaCacheManager',
    'NumbaWarmupManager',
    'ProcessPoolManager',
    'encode_numpy_array',
    'decode_numpy_array',
    'compress_data',
    'decompress_data'
]