#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/13/26 11:56 PM
@File       : __init__.py
@Description: 四号引擎KDE(核密度估计)模块 - 高性能密度估计和LVN检测
"""

from .kde_engine import KDEEngine
from .kde_core import KDECore
from .kde_matrix import KDEMatrixEngine
from .lvn_extractor import LVNExtractor
from .matrix_ops import (
    broadcast_subtract,
    broadcast_gaussian_kernel,
    compute_density_grid
)

__all__ = [
    'KDEEngine',
    'KDECore',
    'KDEMatrixEngine',
    'LVNExtractor',
    'broadcast_subtract',
    'broadcast_gaussian_kernel',
    'compute_density_grid'
]