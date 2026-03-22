#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/13/26 11:56 PM
@File       : __init__.py
@Description: 四号引擎信号生成模块 - 交易信号生成和研究分析
"""

from .signal_generator import TripleASignalGenerator
from .research_generator import ResearchGenerator

__all__ = [
    'TripleASignalGenerator',
    'ResearchGenerator'
]