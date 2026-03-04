#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/4/26
@File       : watchdog.py
@Description: 系统最底层的守护进程。监控 main.py 的存活状态。
"""
import subprocess
import time
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))


def main():
    # 接收终端传入的参数 (比如 live)
    mode = "collect"
    if len(sys.argv) > 1:
        mode = sys.argv[1]

    main_script = os.path.join(current_dir, "main.py")
    cmd = [sys.executable, main_script, mode]

    restart_delay = 5

    while True:
        print(f"🐕 [Watchdog] 正在拉起 Main 集群总司令: {' '.join(cmd)}")

        try:
            # 启动 main.py
            process = subprocess.Popen(cmd)

            # 阻塞等待 main.py 退出
            process.wait()

            exit_code = process.returncode

            if exit_code != 0:
                print(f"❌ [Watchdog] 糟糕！Main.py 发生全局崩溃！(退出码: {exit_code})")
                print(f"⏳ [Watchdog] 将在 {restart_delay} 秒后强行重启整个集群...")
                time.sleep(restart_delay)
            else:
                # 正常退出 (比如手动 kill -15)
                print("🛑 [Watchdog] Main.py 正常安全退出。看门狗任务结束。")
                break

        except KeyboardInterrupt:
            print("\n🛑 [Watchdog] 收到终端停止指令，结束守护。")
            break
        except Exception as e:
            print(f"❌ [Watchdog] 严重异常: {e}")
            time.sleep(restart_delay)


if __name__ == "__main__":
    main()