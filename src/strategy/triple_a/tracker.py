#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Triple-A科考船追踪器
扩展版CSVTracker，支持参数验证和性能评估
"""
import csv
import datetime
import json
import os
import time
from typing import Dict, Any, List, Optional

from src.utils.log import get_logger

logger = get_logger(__name__)


class TripleACSVTracker:
    """Triple-A科考船追踪器"""

    def __init__(self, config, context=None):
        self.config = config
        self.context = context
        self.data_dir = config.research_output_dir

        os.makedirs(self.data_dir, exist_ok=True)

        # 文件路径
        self.signals_file = os.path.join(self.data_dir, f"signals_{config.symbol}.csv")
        self.trades_file = os.path.join(self.data_dir, f"trades_{config.symbol}.csv")
        self.parameters_file = os.path.join(self.data_dir, f"parameters_{config.symbol}.csv")

        # 初始化CSV文件
        self._init_signal_file()
        self._init_trade_file()
        self._init_parameter_file()

        # 活动追踪
        self.active_trackings = []
        self.parameter_experiments = []
        self.current_experiment = None

        logger.info(f"📊 Triple-A科考船初始化完成，数据目录: {self.data_dir}")

    def _init_signal_file(self):
        """初始化信号记录文件"""
        if not os.path.exists(self.signals_file):
            with open(self.signals_file, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'price', 'signal_type', 'phase',
                    'absorption_score', 'accumulation_score', 'aggression_score',
                    'failed_auction_score', 'parameters', 'market_condition'
                ])

    def _init_trade_file(self):
        """初始化交易记录文件"""
        if not os.path.exists(self.trades_file):
            with open(self.trades_file, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'entry_time', 'exit_time', 'entry_price', 'exit_price',
                    'direction', 'pnl', 'pnl_pct', 'stop_loss_hit',
                    'take_profit_hit', 'failed_auction', 'parameters',
                    'is_simulated'
                ])

    def _init_parameter_file(self):
        """初始化参数实验文件"""
        if not os.path.exists(self.parameters_file):
            with open(self.parameters_file, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'experiment_id', 'param_name', 'param_value',
                    'start_time', 'end_time', 'total_trades',
                    'win_rate', 'profit_factor', 'sharpe_ratio',
                    'max_drawdown', 'failed_auction_rate'
                ])

    def add_tracking(self, signal: Dict[str, Any]):
        """记录Triple-A信号"""
        # 创建活动追踪
        tracking = {
            'entry_time': signal.get('timestamp', time.time()),
            'signal_type': signal.get('type', 'UNKNOWN'),
            'phase': signal.get('phase', 'unknown'),
            'entry_price': signal.get('price', 0),
            'score': signal.get('score', 0),
            'parameters': self._get_current_parameters(),
            'max_price': signal.get('price', 0),
            'min_price': signal.get('price', 0),
            'last_update': signal.get('timestamp', time.time()),
            'direction': signal.get('direction', 'UNKNOWN')
        }

        self.active_trackings.append(tracking)

        # 记录到CSV
        self._record_signal(signal)

        logger.debug(f"📝 记录Triple-A信号: {signal.get('type', 'UNKNOWN')}")

    def _record_signal(self, signal: Dict[str, Any]):
        """记录信号到CSV"""
        record = {
            'timestamp': signal.get('timestamp', time.time()),
            'price': signal.get('price', 0),
            'signal_type': signal.get('type', 'UNKNOWN'),
            'phase': signal.get('phase', 'unknown'),
            'absorption_score': signal.get('absorption_score', 0),
            'accumulation_score': signal.get('accumulation_score', 0),
            'aggression_score': signal.get('aggression_score', 0),
            'failed_auction_score': signal.get('failed_auction_score', 0),
            'parameters': json.dumps(self._get_current_parameters()),
            'market_condition': self._analyze_market_condition(signal)
        }

        with open(self.signals_file, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=record.keys())
            writer.writerow(record)

    def record_trade(self, trade: Dict[str, Any], is_simulated: bool = True):
        """记录交易到CSV"""
        record = {
            'entry_time': trade.get('entry_time', time.time()),
            'exit_time': trade.get('exit_time', time.time()),
            'entry_price': trade.get('entry_price', 0),
            'exit_price': trade.get('exit_price', 0),
            'direction': trade.get('direction', 'UNKNOWN'),
            'pnl': trade.get('pnl', 0),
            'pnl_pct': trade.get('pnl_pct', 0),
            'stop_loss_hit': trade.get('stop_loss_hit', False),
            'take_profit_hit': trade.get('take_profit_hit', False),
            'failed_auction': trade.get('failed_auction', False),
            'parameters': json.dumps(self._get_current_parameters()),
            'is_simulated': is_simulated
        }

        with open(self.trades_file, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=record.keys())
            writer.writerow(record)

        logger.debug(f"💾 记录交易: {trade.get('direction', 'UNKNOWN')}, PnL: ${trade.get('pnl', 0):.2f}")

    def update_trackings(self):
        """更新活动追踪（更新最高价/最低价）"""
        if not self.active_trackings:
            return

        current_price = self.context.get_current_price() if self.context else 0

        for tracking in self.active_trackings:
            tracking['max_price'] = max(tracking['max_price'], current_price)
            tracking['min_price'] = min(tracking['min_price'], current_price)
            tracking['last_update'] = time.time()

    def start_parameter_experiment(self, param_name: str, values: List[Any]):
        """启动参数实验"""
        experiment_id = f"exp_{int(time.time())}"

        self.current_experiment = {
            'experiment_id': experiment_id,
            'param_name': param_name,
            'values': values,
            'current_idx': 0,
            'start_time': time.time(),
            'results': []
        }

        self.parameter_experiments.append(self.current_experiment)

        logger.info(f"🔬 启动参数实验: {param_name}, 值: {values}")

    def record_experiment_result(self, metrics: Dict[str, Any]):
        """记录参数实验结果"""
        if not self.current_experiment:
            return

        result = {
            'experiment_id': self.current_experiment['experiment_id'],
            'param_name': self.current_experiment['param_name'],
            'param_value': self.current_experiment['values'][self.current_experiment['current_idx']],
            'start_time': self.current_experiment['start_time'],
            'end_time': time.time(),
            **metrics
        }

        # 记录到CSV
        with open(self.parameters_file, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=result.keys())
            writer.writerow(result)

        self.current_experiment['results'].append(result)
        self.current_experiment['current_idx'] += 1

        logger.info(f"📈 记录实验结果: {result['param_name']}={result['param_value']}, "
                   f"胜率: {metrics.get('win_rate', 0):.2%}")

    def _get_current_parameters(self) -> Dict[str, Any]:
        """获取当前参数配置"""
        if not self.config:
            return {}

        # 提取关键参数
        return {
            'absorption_price_threshold': self.config.absorption_price_threshold,
            'absorption_volume_ratio': self.config.absorption_volume_ratio,
            'accumulation_width_pct': self.config.accumulation_width_pct,
            'aggression_volume_spike': self.config.aggression_volume_spike,
            'failed_auction_detection_threshold': self.config.failed_auction_detection_threshold,
            'risk_pct': self.config.risk_pct,
            'initial_sl_pct': self.config.initial_sl_pct
        }

    def _analyze_market_condition(self, signal: Dict[str, Any]) -> str:
        """分析市场条件（简化版）"""
        # 实际应用中需要分析波动率、趋势等
        price = signal.get('price', 0)
        if price == 0:
            return "UNKNOWN"

        # 简单分类
        volatility = abs(signal.get('price_change', 0) / price * 100 if price > 0 else 0)

        if volatility < 0.5:
            return "LOW_VOLATILITY"
        elif volatility < 2.0:
            return "MEDIUM_VOLATILITY"
        else:
            return "HIGH_VOLATILITY"

    def force_close_all(self):
        """强制关闭所有科考船记录"""
        logger.info("🔒 强制关闭所有Triple-A科考船记录")

        # 关闭所有活动追踪
        for tracking in self.active_trackings:
            # 可以记录未完成的追踪
            pass

        self.active_trackings.clear()

    def get_performance_metrics(self) -> Dict[str, Any]:
        """获取性能指标（需要从交易记录中计算）"""
        # 实际应用中需要读取交易记录并计算指标
        return {
            'total_trades': 0,
            'win_rate': 0,
            'profit_factor': 0,
            'sharpe_ratio': 0,
            'max_drawdown': 0,
            'failed_auction_rate': 0
        }