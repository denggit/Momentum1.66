# !/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26
@File       : auditor.py
@Description: 独立财务审计微服务。负责持久化记录资产，并每天准时发送财务日报。
"""
import asyncio
import datetime
import json
import os
import sys

from dotenv import load_dotenv

current_file = os.path.abspath(__file__)
# 动态获取项目根目录 Momentum1.66
project_root = os.path.abspath(os.path.join(os.path.dirname(current_file), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.log import get_logger
from src.utils.email_sender import send_email
from src.execution.trader import OKXTrader

logger = get_logger("auditor")
load_dotenv()


class DailyAuditor:
    def __init__(self):
        # 默认 09:00 发送
        self.report_time = os.getenv("DAILY_REPORT_TIME", "09:00")
        self.data_dir = os.path.join(project_root, "data")
        os.makedirs(self.data_dir, exist_ok=True)

        # 🌟 两本极其重要的硬盘账本
        self.genesis_file = os.path.join(self.data_dir, "account_genesis.json")
        self.daily_file = os.path.join(self.data_dir, "account_daily.json")

        # 复用你的 Trader 类来获取余额
        self.trader = OKXTrader()

    async def get_current_equity(self):
        await self.trader.fetch_balance()
        return self.trader.available_usdt

    def load_json(self, path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return None

    def save_json(self, path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    async def run_loop(self):
        logger.info(f"👔 [财务审计员] 已上线！将在每天 {self.report_time} 准时核对账本并发送日报。")

        # 启动时初始化对账
        equity = await self.get_current_equity()
        if equity <= 0:
            logger.warning("⚠️ 无法获取初始余额，请检查 API...")

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. 创世记录 (一旦生成，即使重启/宕机也绝对不会被覆盖)
        if not os.path.exists(self.genesis_file) and equity > 0:
            self.save_json(self.genesis_file, {"start_time": now_str, "start_balance": equity})
            logger.warning(f"📝 [创世账本已建立] 记录起步资金: ${equity:.2f}。此数据将永久保留！")

        # 2. 每日快照 (如果不存在则创建)
        if not os.path.exists(self.daily_file) and equity > 0:
            self.save_json(self.daily_file, {"date": str(datetime.date.today()), "balance": equity})

        while True:
            now = datetime.datetime.now()
            target_time = datetime.datetime.strptime(self.report_time, "%H:%M").time()

            # 检查是否到了发送时间 (精确到分钟)
            if now.time().hour == target_time.hour and now.time().minute == target_time.minute:
                await self.generate_and_send_report()
                # 强行休眠 65 秒，完美跨过当前分钟，防止重复发送
                await asyncio.sleep(65)
            else:
                # 没到时间就睡半分钟，极其省 CPU
                await asyncio.sleep(30)

    async def generate_and_send_report(self):
        logger.info("📊 时间到！正在生成每日交易财报...")

        current_equity = await self.get_current_equity()
        genesis = self.load_json(self.genesis_file) or {"start_time": "未知", "start_balance": current_equity}
        daily = self.load_json(self.daily_file) or {"date": "未知", "balance": current_equity}

        # --- 计算今日数据 ---
        today_start = daily['balance']
        today_pnl = current_equity - today_start
        today_return = (today_pnl / today_start * 100) if today_start > 0 else 0.0

        # --- 计算总数据 ---
        total_start = genesis['start_balance']
        total_pnl = current_equity - total_start
        total_return = (total_pnl / total_start * 100) if total_start > 0 else 0.0

        report_text = f"""
======================================
📈 Momentum 1.66 每日实盘财报
======================================
📅 【今日战况】({datetime.date.today()})
• 今日起步资金：${today_start:.2f}
• 当前账户总额：${current_equity:.2f}
• 今日净利润：${today_pnl:.2f}
• 今日收益率：{today_return:.2f}%
*(注: 账户余额变动为绝对真实指标。精确单笔胜率统计将在接入 Fills API 后开放)*

🏆 【历史总览】
• 系统点火时间：{genesis['start_time']}
• 初始总入金：${total_start:.2f}
• 历史总利润：${total_pnl:.2f}
• 历史总收益率：{total_return:.2f}%

注：报表发送完毕，新一天的账本已重新结转。祝今天猎杀顺利！
======================================
"""

        subject = f"📊 Momentum 1.66 每日财报 | 净利: ${today_pnl:.2f} ({today_return:.2f}%)"
        success = await send_email(
            subject=subject,
            content=report_text,
            content_type='plain'
        )

        if success:
            logger.info("✅ 每日财报发送成功！正在结转账本到下一天...")
            # 🌟 最关键的一步：日报发完，立刻把当前的金额覆盖为“下一天的起点”！
            self.save_json(self.daily_file, {"date": str(datetime.date.today()), "balance": current_equity})


if __name__ == "__main__":
    auditor = DailyAuditor()
    asyncio.run(auditor.run_loop())
