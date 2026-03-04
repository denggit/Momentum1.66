#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26
@File       : main.py
@Description: 多引擎集群总司令。负责拉起所有子引擎、进程隔离、统一下发模式、独立故障恢复。
"""
import subprocess
import time
import sys
import os
import asyncio
import signal

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from src.utils.log import get_logger
from src.utils.email_sender import send_trading_signal_email

logger = get_logger("main_commander")

# ==========================================
# 🚀 舰队编制表：在这里注册你未来所有的引擎
# ==========================================
ENGINES = [
    {
        "name": "Engine_3_OrderFlow",
        "script": os.path.join(current_dir, "engines", "engine_3_orderflow", "strategy.py")
    },
    # 未来你想加一号引擎，只需要去掉注释：
    # {
    #     "name": "Engine_1_SMC_Swing",
    #     "script": os.path.join(current_dir, "engines", "engine_1", "strategy.py")
    # },
]


async def notify_crash(engine_name, exit_code):
    details = f"""
🚨 警告：Momentum 1.66 子引擎崩溃！
🤖 崩溃模块: {engine_name}
💀 退出状态码: {exit_code}
⏳ Main总司令正在尝试为您重新拉起该引擎，其他引擎不受影响！
"""
    try:
        await send_trading_signal_email("SYSTEM", f"⚠️ {engine_name} 崩溃重启", 0.0, details)
    except:
        pass


def main():
    # 接收来自 watchdog 的参数 (默认 collect)
    mode = "collect"
    if len(sys.argv) > 1 and sys.argv[1] in ['live', 'collect']:
        mode = sys.argv[1]

    # 🌟 核心改动：只有在 live 模式下，总司令才会拉起“财务审计员”微服务！
    if mode == "live":
        ENGINES.append({
            "name": "Financial_Auditor",
            "script": os.path.join(current_dir, "src", "execution", "auditor.py")
        })

    logger.warning(f"👑 [Main总司令] 上线！全军将进入【{mode.upper()}】模式！")

    active_processes = {}
    restart_delay = 5

    # 1. 初始列队：为每个引擎分配独立的子进程
    for engine in ENGINES:
        cmd = [sys.executable, engine["script"], "--mode", mode]
        logger.info(f"🚀 [Main总司令] 正在点火: {engine['name']}")
        p = subprocess.Popen(cmd)
        active_processes[engine['name']] = {"process": p, "cmd": cmd}

    # 优雅退出处理函数 (传递 kill 信号给所有子进程)
    def handle_sigterm(*args):
        logger.warning("\n🛑 [Main总司令] 收到全军撤退指令！正在向所有子引擎下达安全迫降指令...")
        for name, info in active_processes.items():
            p = info["process"]
            if p.poll() is None:
                p.terminate()  # 相当于向子引擎发送 kill -15
        logger.warning("✅ [Main总司令] 全军迫降指令下达完毕，Main进程退出。")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    # 2. 永不休眠的雷达监控循环
    try:
        while True:
            for name, info in active_processes.items():
                p = info["process"]

                # 如果 poll() 不是 None，说明这个引擎的子进程死了
                if p.poll() is not None:
                    exit_code = p.returncode

                    if exit_code != 0:
                        logger.error(f"❌ [Main总司令] 发现 {name} 意外阵亡！(退出码: {exit_code})")
                        asyncio.run(notify_crash(name, exit_code))
                    else:
                        logger.warning(f"🛑 [Main总司令] 发现 {name} 正常退出。")

                    logger.info(f"⏳ [Main总司令] {restart_delay} 秒后尝试重新部署 {name}...")
                    time.sleep(restart_delay)

                    # 重新拉起死掉的那个引擎，绝对不影响其他活着的引擎
                    logger.info(f"🔄 [Main总司令] 正在重新拉起: {name}")
                    new_p = subprocess.Popen(info["cmd"])
                    active_processes[name]["process"] = new_p

            time.sleep(2)  # 每 2 秒巡视一圈

    except Exception as e:
        logger.error(f"❌ [Main总司令] 监控循环发生异常: {e}")
        handle_sigterm()


if __name__ == "__main__":
    main()