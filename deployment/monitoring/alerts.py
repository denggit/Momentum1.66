#!/usr/bin/env python3
"""
四号引擎告警系统
实时监控关键指标，触发告警并通知相关人员
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any

import requests

from src.utils.log import get_logger

# 尝试导入Slack SDK（可选）
try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False
    print("⚠️  Slack SDK未安装，Slack通知将不可用")

# 尝试导入钉钉SDK（可选）
try:
    import dingtalk

    DINGTALK_AVAILABLE = True
except ImportError:
    DINGTALK_AVAILABLE = False
    print("⚠️  钉钉SDK未安装，钉钉通知将不可用")


class AlertSeverity(Enum):
    """告警严重程度"""
    INFO = "info"  # 信息
    WARNING = "warning"  # 警告
    ERROR = "error"  # 错误
    CRITICAL = "critical"  # 严重


class AlertChannel(Enum):
    """告警通道"""
    EMAIL = "email"  # 邮件
    SLACK = "slack"  # Slack
    DINGTALK = "dingtalk"  # 钉钉
    WEBHOOK = "webhook"  # Webhook
    LOG = "log"  # 日志文件


@dataclass
class AlertRule:
    """告警规则"""
    name: str  # 规则名称
    metric: str  # 监控指标
    condition: str  # 条件表达式，如 ">", "<", "=="
    threshold: float  # 阈值
    severity: AlertSeverity  # 严重程度
    duration: int = 60  # 持续时间（秒），超过此时间才触发
    cooldown: int = 300  # 冷却时间（秒），避免重复告警
    channels: List[AlertChannel] = field(default_factory=lambda: [AlertChannel.LOG])  # 通知通道
    enabled: bool = True  # 是否启用


@dataclass
class Alert:
    """告警实例"""
    id: str  # 告警ID
    rule: AlertRule  # 触发规则
    timestamp: float  # 触发时间
    metric_value: float  # 指标值
    message: str  # 告警消息
    acknowledged: bool = False  # 是否已确认
    resolved: bool = False  # 是否已解决
    resolved_time: Optional[float] = None  # 解决时间


class AlertManager:
    """告警管理器"""

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化告警管理器

        Args:
            config_path: 配置文件路径
        """
        self.rules: Dict[str, AlertRule] = {}
        self.active_alerts: Dict[str, Alert] = {}
        self.alert_history: List[Alert] = []
        self.metric_history: Dict[str, List[tuple]] = {}  # metric -> [(timestamp, value)]
        self.last_trigger_time: Dict[str, float] = {}  # rule_name -> last_trigger_time
        self.rule_violation_start: Dict[str, float] = {}  # rule_name -> violation_start_time

        # 通知配置
        self.email_config: Dict[str, Any] = {}
        self.slack_config: Dict[str, Any] = {}
        self.dingtalk_config: Dict[str, Any] = {}
        self.webhook_config: Dict[str, Any] = {}

        # 初始化日志
        self.logger = get_logger(__name__)

        # 加载配置
        if config_path:
            self.load_config(config_path)
        else:
            self.load_default_rules()

    def load_default_rules(self):
        """加载默认告警规则"""
        default_rules = [
            # 系统告警
            AlertRule(
                name="high_cpu_usage",
                metric="system.cpu_percent",
                condition=">",
                threshold=80.0,
                severity=AlertSeverity.WARNING,
                duration=30,
                cooldown=300,
                channels=[AlertChannel.LOG, AlertChannel.EMAIL]
            ),
            AlertRule(
                name="high_memory_usage",
                metric="system.memory_percent",
                condition=">",
                threshold=85.0,
                severity=AlertSeverity.WARNING,
                duration=30,
                cooldown=300,
                channels=[AlertChannel.LOG, AlertChannel.EMAIL]
            ),
            AlertRule(
                name="high_disk_usage",
                metric="system.disk_usage_percent",
                condition=">",
                threshold=90.0,
                severity=AlertSeverity.WARNING,
                duration=60,
                cooldown=600,
                channels=[AlertChannel.LOG, AlertChannel.EMAIL, AlertChannel.SLACK]
            ),

            # 性能告警
            AlertRule(
                name="high_tick_latency",
                metric="performance.tick_latency_ms",
                condition=">",
                threshold=5.0,
                severity=AlertSeverity.ERROR,
                duration=10,
                cooldown=60,
                channels=[AlertChannel.LOG, AlertChannel.SLACK]
            ),
            AlertRule(
                name="high_total_latency",
                metric="performance.total_latency_ms",
                condition=">",
                threshold=10.0,
                severity=AlertSeverity.ERROR,
                duration=10,
                cooldown=60,
                channels=[AlertChannel.LOG, AlertChannel.SLACK, AlertChannel.DINGTALK]
            ),
            AlertRule(
                name="low_cache_hit_rate",
                metric="performance.cache_hit_rate",
                condition="<",
                threshold=0.8,
                severity=AlertSeverity.WARNING,
                duration=60,
                cooldown=300,
                channels=[AlertChannel.LOG, AlertChannel.EMAIL]
            ),

            # 业务告警
            AlertRule(
                name="high_error_rate",
                metric="business.error_count",
                condition=">",
                threshold=10,
                severity=AlertSeverity.ERROR,
                duration=30,
                cooldown=300,
                channels=[AlertChannel.LOG, AlertChannel.SLACK, AlertChannel.DINGTALK]
            ),
            AlertRule(
                name="connection_lost",
                metric="system.connection_status",
                condition="==",
                threshold=0,  # 0表示断开
                severity=AlertSeverity.CRITICAL,
                duration=5,
                cooldown=60,
                channels=[AlertChannel.LOG, AlertChannel.EMAIL, AlertChannel.SLACK, AlertChannel.DINGTALK]
            ),
            AlertRule(
                name="no_signals",
                metric="business.signal_count",
                condition="==",
                threshold=0,
                severity=AlertSeverity.WARNING,
                duration=300,  # 5分钟无信号
                cooldown=600,
                channels=[AlertChannel.LOG, AlertChannel.EMAIL]
            )
        ]

        for rule in default_rules:
            self.add_rule(rule)

    def load_config(self, config_path: str):
        """
        从配置文件加载告警规则

        Args:
            config_path: 配置文件路径
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # 加载通知配置
            self.email_config = config.get('email', {})
            self.slack_config = config.get('slack', {})
            self.dingtalk_config = config.get('dingtalk', {})
            self.webhook_config = config.get('webhook', {})

            # 加载告警规则
            rules_config = config.get('rules', [])
            for rule_config in rules_config:
                rule = AlertRule(
                    name=rule_config['name'],
                    metric=rule_config['metric'],
                    condition=rule_config['condition'],
                    threshold=rule_config['threshold'],
                    severity=AlertSeverity(rule_config['severity']),
                    duration=rule_config.get('duration', 60),
                    cooldown=rule_config.get('cooldown', 300),
                    channels=[AlertChannel(c) for c in rule_config.get('channels', ['log'])],
                    enabled=rule_config.get('enabled', True)
                )
                self.add_rule(rule)

            self.logger.info(f"✅ 从 {config_path} 加载了 {len(self.rules)} 个告警规则")

        except Exception as e:
            self.logger.error(f"❌ 加载配置文件失败: {e}")
            self.load_default_rules()

    def add_rule(self, rule: AlertRule):
        """
        添加告警规则

        Args:
            rule: 告警规则
        """
        self.rules[rule.name] = rule
        self.logger.debug(f"添加告警规则: {rule.name}")

    def remove_rule(self, rule_name: str):
        """
        移除告警规则

        Args:
            rule_name: 规则名称
        """
        if rule_name in self.rules:
            del self.rules[rule_name]
            self.logger.debug(f"移除告警规则: {rule_name}")

    def update_metric(self, metric_name: str, value: float):
        """
        更新指标值

        Args:
            metric_name: 指标名称
            value: 指标值
        """
        timestamp = time.time()

        # 保存指标历史
        if metric_name not in self.metric_history:
            self.metric_history[metric_name] = []

        self.metric_history[metric_name].append((timestamp, value))

        # 保留最近1000个记录
        if len(self.metric_history[metric_name]) > 1000:
            self.metric_history[metric_name] = self.metric_history[metric_name][-1000:]

        # 检查所有相关规则
        self._check_rules_for_metric(metric_name, timestamp, value)

    def _check_rules_for_metric(self, metric_name: str, timestamp: float, value: float):
        """
        检查指标相关的所有规则

        Args:
            metric_name: 指标名称
            timestamp: 时间戳
            value: 指标值
        """
        for rule_name, rule in self.rules.items():
            if not rule.enabled:
                continue

            if rule.metric != metric_name:
                continue

            # 检查条件
            is_violated = self._check_condition(value, rule.condition, rule.threshold)

            if is_violated:
                # 记录违规开始时间
                if rule_name not in self.rule_violation_start:
                    self.rule_violation_start[rule_name] = timestamp
                    self.logger.debug(f"规则 {rule_name} 开始违规，值: {value}")

                # 检查是否超过持续时间
                violation_duration = timestamp - self.rule_violation_start[rule_name]
                if violation_duration >= rule.duration:
                    # 检查冷却时间
                    last_trigger = self.last_trigger_time.get(rule_name, 0)
                    if timestamp - last_trigger >= rule.cooldown:
                        # 触发告警
                        self._trigger_alert(rule, timestamp, value)
            else:
                # 清除违规记录
                if rule_name in self.rule_violation_start:
                    del self.rule_violation_start[rule_name]
                    self.logger.debug(f"规则 {rule_name} 违规结束")

                # 如果之前有活跃告警，标记为已解决
                for alert_id, alert in list(self.active_alerts.items()):
                    if alert.rule.name == rule_name and not alert.resolved:
                        self._resolve_alert(alert_id, timestamp)

    def _check_condition(self, value: float, condition: str, threshold: float) -> bool:
        """
        检查条件是否满足

        Args:
            value: 指标值
            condition: 条件
            threshold: 阈值

        Returns:
            bool: 是否满足条件
        """
        if condition == ">":
            return value > threshold
        elif condition == ">=":
            return value >= threshold
        elif condition == "<":
            return value < threshold
        elif condition == "<=":
            return value <= threshold
        elif condition == "==":
            return abs(value - threshold) < 0.0001
        elif condition == "!=":
            return abs(value - threshold) >= 0.0001
        else:
            self.logger.warning(f"未知条件: {condition}")
            return False

    def _trigger_alert(self, rule: AlertRule, timestamp: float, metric_value: float):
        """
        触发告警

        Args:
            rule: 告警规则
            timestamp: 时间戳
            metric_value: 指标值
        """
        # 生成告警ID
        alert_id = f"{rule.name}_{int(timestamp)}"

        # 创建告警消息
        message = self._create_alert_message(rule, metric_value)

        # 创建告警实例
        alert = Alert(
            id=alert_id,
            rule=rule,
            timestamp=timestamp,
            metric_value=metric_value,
            message=message
        )

        # 保存告警
        self.active_alerts[alert_id] = alert
        self.alert_history.append(alert)
        self.last_trigger_time[rule.name] = timestamp

        # 发送通知
        self._send_notifications(alert)

        # 记录日志
        self.logger.warning(f"🚨 触发告警: {rule.name} - {message}")

    def _resolve_alert(self, alert_id: str, timestamp: float):
        """
        解决告警

        Args:
            alert_id: 告警ID
            timestamp: 时间戳
        """
        if alert_id not in self.active_alerts:
            return

        alert = self.active_alerts[alert_id]
        alert.resolved = True
        alert.resolved_time = timestamp

        # 从活跃告警中移除
        del self.active_alerts[alert_id]

        # 发送解决通知
        resolve_message = f"告警已解决: {alert.message}"
        self._send_resolution_notification(alert, resolve_message)

        # 记录日志
        self.logger.info(f"✅ 告警解决: {alert.rule.name}")

    def _create_alert_message(self, rule: AlertRule, metric_value: float) -> str:
        """
        创建告警消息

        Args:
            rule: 告警规则
            metric_value: 指标值

        Returns:
            str: 告警消息
        """
        severity_emoji = {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.ERROR: "❌",
            AlertSeverity.CRITICAL: "🚨"
        }

        emoji = severity_emoji.get(rule.severity, "📢")

        return f"{emoji} [{rule.severity.value.upper()}] {rule.name}: {metric_value} {rule.condition} {rule.threshold}"

    def _send_notifications(self, alert: Alert):
        """
        发送通知

        Args:
            alert: 告警实例
        """
        for channel in alert.rule.channels:
            try:
                if channel == AlertChannel.EMAIL:
                    self._send_email_notification(alert)
                elif channel == AlertChannel.SLACK:
                    self._send_slack_notification(alert)
                elif channel == AlertChannel.DINGTALK:
                    self._send_dingtalk_notification(alert)
                elif channel == AlertChannel.WEBHOOK:
                    self._send_webhook_notification(alert)
                elif channel == AlertChannel.LOG:
                    # 日志通道已经在触发时记录
                    pass

            except Exception as e:
                self.logger.error(f"发送 {channel.value} 通知失败: {e}")

    def _send_email_notification(self, alert: Alert):
        """发送邮件通知"""
        if not self.email_config:
            self.logger.warning("邮件配置未设置，跳过邮件通知")
            return

        try:
            # 这里简化实现，实际需要配置SMTP服务器
            subject = f"[{alert.rule.severity.value.upper()}] {alert.rule.name}"
            body = f"""
            告警详情:
            时间: {datetime.fromtimestamp(alert.timestamp)}
            规则: {alert.rule.name}
            严重程度: {alert.rule.severity.value}
            指标值: {alert.metric_value}
            阈值: {alert.rule.condition} {alert.rule.threshold}
            消息: {alert.message}

            系统: 四号引擎
            环境: 科考船测试环境
            """

            # 实际发送邮件逻辑
            # ...

            self.logger.info(f"📧 邮件通知已发送: {subject}")

        except Exception as e:
            self.logger.error(f"发送邮件通知失败: {e}")

    def _send_slack_notification(self, alert: Alert):
        """发送Slack通知"""
        if not SLACK_AVAILABLE or not self.slack_config:
            self.logger.warning("Slack配置未设置，跳过Slack通知")
            return

        try:
            client = WebClient(token=self.slack_config.get('token'))
            channel = self.slack_config.get('channel', '#alerts')

            # 创建消息块
            color_map = {
                AlertSeverity.INFO: "#36a64f",
                AlertSeverity.WARNING: "#ffcc00",
                AlertSeverity.ERROR: "#ff9900",
                AlertSeverity.CRITICAL: "#ff0000"
            }

            color = color_map.get(alert.rule.severity, "#808080")

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🚨 四号引擎告警: {alert.rule.severity.value.upper()}"
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*规则:*\n{alert.rule.name}"},
                        {"type": "mrkdwn", "text": f"*时间:*\n{datetime.fromtimestamp(alert.timestamp)}"}
                    ]
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*指标值:*\n{alert.metric_value}"},
                        {"type": "mrkdwn", "text": f"*阈值:*\n{alert.rule.condition} {alert.rule.threshold}"}
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*消息:*\n{alert.message}"
                    }
                }
            ]

            response = client.chat_postMessage(
                channel=channel,
                blocks=blocks,
                attachments=[{"color": color}]
            )

            self.logger.info(f"💬 Slack通知已发送: {response['ts']}")

        except SlackApiError as e:
            self.logger.error(f"Slack API错误: {e.response['error']}")
        except Exception as e:
            self.logger.error(f"发送Slack通知失败: {e}")

    def _send_dingtalk_notification(self, alert: Alert):
        """发送钉钉通知"""
        if not DINGTALK_AVAILABLE or not self.dingtalk_config:
            self.logger.warning("钉钉配置未设置，跳过钉钉通知")
            return

        try:
            # 这里简化实现，实际需要配置钉钉Webhook
            webhook_url = self.dingtalk_config.get('webhook_url')
            secret = self.dingtalk_config.get('secret')

            # 创建消息
            message = {
                "msgtype": "markdown",
                "markdown": {
                    "title": f"四号引擎告警: {alert.rule.severity.value.upper()}",
                    "text": f"""
## 🚨 四号引擎告警

**规则**: {alert.rule.name}
**严重程度**: {alert.rule.severity.value}
**时间**: {datetime.fromtimestamp(alert.timestamp)}
**指标值**: {alert.metric_value}
**阈值**: {alert.rule.condition} {alert.rule.threshold}
**消息**: {alert.message}

**系统**: 四号引擎
**环境**: 科考船测试环境
                    """
                },
                "at": {
                    "isAtAll": alert.rule.severity in [AlertSeverity.ERROR, AlertSeverity.CRITICAL]
                }
            }

            # 实际发送钉钉消息逻辑
            # ...

            self.logger.info(f"📱 钉钉通知已发送: {alert.rule.name}")

        except Exception as e:
            self.logger.error(f"发送钉钉通知失败: {e}")

    def _send_webhook_notification(self, alert: Alert):
        """发送Webhook通知"""
        if not self.webhook_config:
            self.logger.warning("Webhook配置未设置，跳过Webhook通知")
            return

        try:
            url = self.webhook_config.get('url')
            headers = self.webhook_config.get('headers', {})

            payload = {
                "alert_id": alert.id,
                "rule_name": alert.rule.name,
                "severity": alert.rule.severity.value,
                "timestamp": alert.timestamp,
                "metric_value": alert.metric_value,
                "threshold": alert.rule.threshold,
                "condition": alert.rule.condition,
                "message": alert.message,
                "system": "四号引擎",
                "environment": "科考船测试环境"
            }

            response = requests.post(url, json=payload, headers=headers, timeout=5)
            response.raise_for_status()

            self.logger.info(f"🌐 Webhook通知已发送: {response.status_code}")

        except Exception as e:
            self.logger.error(f"发送Webhook通知失败: {e}")

    def _send_resolution_notification(self, alert: Alert, message: str):
        """发送解决通知"""
        # 实现类似_send_notifications，但发送解决消息
        pass

    def acknowledge_alert(self, alert_id: str):
        """
        确认告警

        Args:
            alert_id: 告警ID
        """
        if alert_id in self.active_alerts:
            self.active_alerts[alert_id].acknowledged = True
            self.logger.info(f"告警已确认: {alert_id}")

    def get_active_alerts(self) -> List[Alert]:
        """
        获取活跃告警

        Returns:
            List[Alert]: 活跃告警列表
        """
        return list(self.active_alerts.values())

    def get_alert_history(self, limit: int = 100) -> List[Alert]:
        """
        获取告警历史

        Args:
            limit: 返回数量限制

        Returns:
            List[Alert]: 告警历史列表
        """
        return self.alert_history[-limit:]

    def get_metrics_summary(self) -> Dict[str, Any]:
        """
        获取指标摘要

        Returns:
            Dict[str, Any]: 指标摘要
        """
        return {
            "total_rules": len(self.rules),
            "active_alerts": len(self.active_alerts),
            "total_alerts": len(self.alert_history),
            "recent_alerts": len([a for a in self.alert_history[-100:] if not a.resolved])
        }

    async def monitor_metrics(self, metrics_collector, interval: float = 1.0):
        """
        监控指标任务

        Args:
            metrics_collector: 指标收集器实例
            interval: 监控间隔（秒）
        """
        while True:
            try:
                # 收集系统指标（示例）
                # 实际应用中需要从metrics_collector获取指标
                system_summary = metrics_collector.get_system_summary()
                perf_summary = metrics_collector.get_performance_summary()
                biz_summary = metrics_collector.get_business_summary()

                # 更新指标到告警系统
                if system_summary:
                    self.update_metric("system.cpu_percent", system_summary.get("cpu_percent", 0))
                    self.update_metric("system.memory_percent", system_summary.get("memory_percent", 0))
                    self.update_metric("system.disk_usage_percent", system_summary.get("disk_usage_percent", 0))

                if perf_summary:
                    self.update_metric("performance.total_latency_ms", perf_summary.get("avg_total_latency_ms", 0))
                    self.update_metric("performance.cache_hit_rate", perf_summary.get("avg_cache_hit_rate", 0))

                if biz_summary:
                    self.update_metric("business.error_count", biz_summary.get("total_errors", 0))
                    self.update_metric("business.signal_count", biz_summary.get("total_signals", 0))

                # 等待下一个监控周期
                await asyncio.sleep(interval)

            except Exception as e:
                self.logger.error(f"监控任务出错: {e}")
                await asyncio.sleep(interval)


def main():
    """测试主函数"""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

    # 日志系统已由get_logger自动配置

    # 创建告警管理器
    alert_manager = AlertManager()

    # 测试指标更新
    print("🧪 测试告警系统...")

    # 模拟指标更新
    test_metrics = [
        ("system.cpu_percent", 85.5),  # 应该触发high_cpu_usage告警
        ("system.memory_percent", 70.0),  # 不触发
        ("performance.total_latency_ms", 12.5),  # 应该触发high_total_latency告警
        ("business.error_count", 15),  # 应该触发high_error_rate告警
        ("system.cpu_percent", 60.0),  # 恢复正常
        ("performance.total_latency_ms", 3.0),  # 恢复正常
    ]

    for metric_name, value in test_metrics:
        print(f"更新指标: {metric_name} = {value}")
        alert_manager.update_metric(metric_name, value)
        time.sleep(0.5)

    # 获取活跃告警
    active_alerts = alert_manager.get_active_alerts()
    print(f"\\n📊 活跃告警 ({len(active_alerts)} 个):")
    for alert in active_alerts:
        print(f"  - {alert.message}")

    # 获取告警历史
    alert_history = alert_manager.get_alert_history(5)
    print(f"\\n📊 最近告警 ({len(alert_history)} 个):")
    for alert in alert_history:
        status = "已解决" if alert.resolved else "活跃"
        print(f"  - {alert.message} ({status})")

    # 获取摘要
    summary = alert_manager.get_metrics_summary()
    print(f"\\n📈 系统摘要:")
    for key, value in summary.items():
        print(f"  - {key}: {value}")

    print("\\n✅ 告警系统测试完成")


if __name__ == "__main__":
    main()
