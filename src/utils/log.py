#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
日志模块，提供按日期分割的日志文件功能。
每天一个日志文件，程序运行时如果日期变更会自动切换到新的日志文件。
"""
import logging
import logging.handlers
import os
import sys

_setup_done = False


def setup_logging(log_level=logging.INFO, log_dir='logs'):
    """
    配置根日志记录器。

    Args:
        log_level: 日志级别，默认为 INFO
        log_dir: 日志文件存放目录，默认为 'logs'
    """
    global _setup_done
    if _setup_done:
        return

    # 获取根日志器
    root_logger = logging.getLogger()

    # 移除所有现有的处理器，确保我们拥有完整的控制权
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 创建日志目录
    os.makedirs(log_dir, exist_ok=True)

    # 设置根日志器级别
    root_logger.setLevel(log_level)

    # 日志格式
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 按日期轮转的文件处理器（每天一个文件）
    log_file = os.path.join(log_dir, 'app.log')
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_file,
        when='midnight',  # 每天午夜轮转
        interval=1,  # 间隔1天
        backupCount=30,  # 保留最近30天的日志
        encoding='utf-8'
    )
    file_handler.suffix = '%Y-%m-%d'  # 日志文件后缀格式
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 记录初始日志
    root_logger.info(f'日志系统初始化完成，日志目录: {os.path.abspath(log_dir)}， 日志等级：{log_level}')

    _setup_done = True


def get_logger(name, log_level=logging.DEBUG):
    """
    获取指定名称的日志记录器。

    Args:
        name: 日志记录器名称，通常使用 __name__

    Returns:
        logging.Logger 实例
    """
    # 确保日志系统已初始化
    setup_logging(log_level=log_level)

    return logging.getLogger(name)


# 导入此模块时自动初始化日志系统
setup_logging(log_level=logging.DEBUG)

# 提供便捷的全局日志记录器
logger = get_logger(__name__)
