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

def slay_zombies():
    """🌟 精准狙击：只清理当前项目目录下的子进程，绝不误杀其他程序"""
    print(f"🧹 [Watchdog] 正在扫描并清理当前项目 ({current_dir}) 的残留进程...")
    
    # 拼装出独一无二的绝对路径
    main_path = os.path.join(current_dir, "main.py")
    auditor_path = os.path.join(current_dir, "src", "execution", "auditor.py")
    engines_dir = os.path.join(current_dir, "engines")
    
    # 🌟 核心修复：pkill -f 配合单引号包裹绝对路径，确保 100% 匹配本项目！
    # 这样就算别的目录也有 auditor.py，也绝对不会被误杀。
    os.system(f"pkill -f '{main_path}'")
    os.system(f"pkill -f '{auditor_path}'")
    os.system(f"pkill -f '{engines_dir}.*strategy.py'") 
    
    time.sleep(1) # 给操作系统 1 秒钟回收内存
    

def main():
    # 🌟 启动看门狗的第一件事：清理门户！
    slay_zombies()

    # 解析命令行参数
    mode = "collect"
    symbol = "ETH-USDT-SWAP"

    # 更健壮的参数解析，支持任意顺序的 --mode 和 --symbol
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--mode" and i + 1 < len(sys.argv):
            mode = sys.argv[i + 1]
            i += 2
        elif arg == "--symbol" and i + 1 < len(sys.argv):
            symbol = sys.argv[i + 1]
            i += 2
        elif arg.startswith("--"):
            # 未知参数，跳过
            i += 1
            if i < len(sys.argv) and not sys.argv[i].startswith("--"):
                i += 1  # 跳过参数值
        elif arg in ["collect", "live"]:  # 向后兼容：直接参数模式
            mode = arg
            i += 1
        else:
            i += 1

    main_script = os.path.join(current_dir, "main.py")
    cmd = [sys.executable, main_script, "--mode", mode, "--symbol", symbol]

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
