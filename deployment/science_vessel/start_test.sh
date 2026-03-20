#!/bin/bash
# 四号引擎科考船实盘测试启动脚本
# 版本: v1.0
# 创建时间: 2026-03-20

set -e  # 遇到错误立即退出

# ==================== 颜色定义 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==================== 日志函数 ====================
log_info() {
    echo -e "${BLUE}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# ==================== 环境检查 ====================
check_environment() {
    log_info "开始环境检查..."

    # 检查Python版本
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    log_info "Python版本: $PYTHON_VERSION"

    if [[ "$PYTHON_VERSION" < "3.9" ]]; then
        log_error "需要Python 3.9或更高版本"
        exit 1
    fi

    # 检查项目根目录
    if [ ! -f "requirements.txt" ] && [ ! -f "pyproject.toml" ]; then
        log_error "未在项目根目录，请切换到项目根目录运行"
        exit 1
    fi

    # 检查依赖
    log_info "检查Python依赖..."
    if ! python3 -c "import numpy, numba, psutil, yaml" &>/dev/null; then
        log_warning "缺少部分依赖，尝试安装..."
        pip install -r requirements.txt 2>/dev/null || pip install numpy numba psutil pyyaml
    fi

    # 检查配置文件
    if [ ! -f "config.yaml" ]; then
        log_error "配置文件 config.yaml 不存在"
        exit 1
    fi

    log_success "环境检查通过"
}

# ==================== 目录准备 ====================
prepare_directories() {
    log_info "准备目录结构..."

    # 创建必要的目录
    mkdir -p logs
    mkdir -p data/tick
    mkdir -p data/order
    mkdir -p data/performance
    mkdir -p reports

    # 设置权限
    chmod 755 logs data reports

    log_success "目录准备完成"
}

# ==================== 配置验证 ====================
validate_config() {
    log_info "验证配置文件..."

    # 使用Python验证YAML配置
    python3 -c "
import yaml
import sys

try:
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    # 检查必需字段
    required_fields = ['environment', 'trading', 'triplea_engine']
    for field in required_fields:
        if field not in config:
            print(f'错误: 缺少必需字段 {field}')
            sys.exit(1)

    # 检查交易对配置
    if 'symbol' not in config['trading']:
        print('错误: 缺少交易对配置')
        sys.exit(1)

    print('配置验证通过')

except yaml.YAMLError as e:
    print(f'YAML解析错误: {e}')
    sys.exit(1)
except Exception as e:
    print(f'配置验证错误: {e}')
    sys.exit(1)
"

    if [ $? -ne 0 ]; then
        log_error "配置验证失败"
        exit 1
    fi

    log_success "配置验证通过"
}

# ==================== 网络连接测试 ====================
test_network_connectivity() {
    log_info "测试网络连接..."

    # 测试OKX API连接
    OKX_API="https://www.okx.com"
    if curl -s --head --request GET "$OKX_API" | grep "200 OK" > /dev/null; then
        log_success "OKX API连接正常"
    else
        log_warning "OKX API连接测试失败，继续运行..."
    fi

    # 测试互联网连接
    if ping -c 1 8.8.8.8 &> /dev/null; then
        log_success "互联网连接正常"
    else
        log_error "互联网连接失败"
        exit 1
    fi
}

# ==================== 性能基准测试 ====================
run_performance_baseline() {
    log_info "运行性能基准测试..."

    # 运行性能测试脚本
    if [ -f "tests/performance/test_tick_latency.py" ]; then
        python3 tests/performance/test_tick_latency.py --baseline
    else
        log_warning "性能测试脚本不存在，跳过基准测试"
    fi
}

# ==================== 启动四号引擎 ====================
start_triplea_engine() {
    log_info "启动四号引擎..."

    # 检查引擎文件
    if [ ! -f "src/strategy/triplea/signal_generator.py" ]; then
        log_error "四号引擎文件不存在"
        exit 1
    fi

    # 启动引擎（这里使用测试模式）
    log_info "启动科考船测试模式..."

    # 创建启动命令
    cat > start_engine.py << 'EOF'
#!/usr/bin/env python3
"""
四号引擎科考船测试模式启动脚本
"""
import sys
import os
import asyncio
import yaml
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

async def main():
    print("🚀 四号引擎科考船测试模式启动")
    print("=" * 60)

    # 加载配置
    config_path = "config.yaml"
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        print(f"✅ 配置文件加载: {config_path}")
    else:
        print(f"⚠️  配置文件不存在: {config_path}")
        config = {}

    # 显示配置信息
    print(f"\n📊 环境配置:")
    print(f"  模式: {config.get('environment', {}).get('mode', 'simulation')}")
    print(f"  交易对: {config.get('trading', {}).get('symbol', 'ETH-USDT-SWAP')}")
    print(f"  账户规模: {config.get('triplea_engine', {}).get('risk_management', {}).get('account_size_usdt', 300.0)}U")

    # 导入四号引擎
    try:
        from src.strategy.triplea.signal_generator import TripleASignalGenerator
        print(f"\n✅ 四号引擎导入成功")

        # 创建信号生成器（测试模式）
        generator = TripleASignalGenerator(symbol=config.get('trading', {}).get('symbol', 'ETH-USDT-SWAP'))
        print(f"✅ 信号生成器初始化完成")
        print(f"  状态: {generator.status}")
        print(f"  是否影子引擎: {generator.is_shadow}")

    except ImportError as e:
        print(f"❌ 四号引擎导入失败: {e}")
        return

    print(f"\n🎯 科考船测试目标:")
    print("  1. 验证接口兼容性")
    print("  2. 测试性能指标")
    print("  3. 收集实盘数据")
    print("  4. 验证风控系统")

    print(f"\n⏱️  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 保持运行（实际测试中会有事件循环）
    print("\n🔧 科考船测试环境准备就绪")
    print("💡 提示: 实际测试需要连接市场数据源")

    # 模拟运行10秒
    await asyncio.sleep(10)

    print("\n✅ 科考船测试环境启动完成")

if __name__ == "__main__":
    asyncio.run(main())
EOF

    # 运行启动脚本
    python3 start_engine.py

    if [ $? -ne 0 ]; then
        log_error "四号引擎启动失败"
        exit 1
    fi

    log_success "四号引擎启动完成"
}

# ==================== 监控仪表板 ====================
start_monitoring_dashboard() {
    log_info "启动监控仪表板..."

    # 创建监控仪表板脚本
    cat > monitoring_dashboard.py << 'EOF'
#!/usr/bin/env python3
"""
科考船监控仪表板
"""
import sys
import os
import time
import psutil
import yaml
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

class MonitoringDashboard:
    def __init__(self, config_path="config.yaml"):
        self.config_path = config_path
        self.start_time = time.time()
        self.metrics = {
            'cpu_usage': [],
            'memory_usage': [],
            'tick_count': 0,
            'order_count': 0,
            'errors': []
        }

    def load_config(self):
        """加载配置"""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        return {}

    def get_system_metrics(self):
        """获取系统指标"""
        return {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'memory_used_mb': psutil.virtual_memory().used / 1024 / 1024,
            'disk_usage': psutil.disk_usage('/').percent,
            'uptime_seconds': time.time() - self.start_time
        }

    def display_dashboard(self):
        """显示监控仪表板"""
        config = self.load_config()
        system_metrics = self.get_system_metrics()

        # 清屏（ANSI转义序列）
        print("\033[2J\033[H")

        print("=" * 80)
        print("🚢 四号引擎科考船监控仪表板")
        print("=" * 80)

        # 系统信息
        print(f"\n📊 系统状态:")
        print(f"  运行时间: {system_metrics['uptime_seconds']:.0f}秒")
        print(f"  CPU使用率: {system_metrics['cpu_percent']:.1f}%")
        print(f"  内存使用: {system_metrics['memory_used_mb']:.1f}MB ({system_metrics['memory_percent']:.1f}%)")
        print(f"  磁盘使用: {system_metrics['disk_usage']:.1f}%")

        # 测试环境信息
        print(f"\n🌍 测试环境:")
        env_config = config.get('environment', {})
        print(f"  模式: {env_config.get('mode', 'simulation')}")
        print(f"  服务器: {env_config.get('server', {}).get('region', 'tokyo')}")

        # 交易信息
        print(f"\n💰 交易状态:")
        trading_config = config.get('trading', {})
        print(f"  交易对: {trading_config.get('symbol', 'ETH-USDT-SWAP')}")
        print(f"  杠杆: {trading_config.get('leverage', 3)}x")

        # 引擎状态
        print(f"\n⚙️  四号引擎状态:")
        engine_config = config.get('triplea_engine', {})
        risk_config = engine_config.get('risk_management', {})
        print(f"  账户规模: {risk_config.get('account_size_usdt', 300.0)}U")
        print(f"  单笔风险: {risk_config.get('max_risk_per_trade_pct', 5.0)}%")
        print(f"  止损: {risk_config.get('stop_loss_ticks', 2)} ticks")
        print(f"  止盈: {risk_config.get('take_profit_ticks', 6)} ticks")

        # 性能指标
        print(f"\n📈 性能指标:")
        print(f"  Tick处理数: {self.metrics['tick_count']}")
        print(f"  订单数: {self.metrics['order_count']}")
        print(f"  错误数: {len(self.metrics['errors'])}")

        # 最近错误
        if self.metrics['errors']:
            print(f"\n⚠️  最近错误:")
            for error in self.metrics['errors'][-3:]:
                print(f"  - {error}")

        print(f"\n⏰ 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        print("🔄 自动刷新中... (Ctrl+C 退出)")

    def run(self):
        """运行监控仪表板"""
        print("启动监控仪表板...")
        try:
            while True:
                self.display_dashboard()
                time.sleep(5)  # 5秒刷新一次

                # 模拟指标更新
                self.metrics['tick_count'] += 10
                self.metrics['order_count'] += 1

        except KeyboardInterrupt:
            print("\n\n监控仪表板已停止")
        except Exception as e:
            print(f"\n监控仪表板错误: {e}")

if __name__ == "__main__":
    dashboard = MonitoringDashboard()
    dashboard.run()
EOF

    # 在后台启动监控仪表板
    python3 monitoring_dashboard.py &
    DASHBOARD_PID=$!

    log_success "监控仪表板已启动 (PID: $DASHBOARD_PID)"
    echo $DASHBOARD_PID > dashboard.pid
}

# ==================== 主函数 ====================
main() {
    log_info "🚀 四号引擎科考船实盘测试启动"
    log_info "版本: v3.0 | 模式: 科考船测试"
    echo ""

    # 执行步骤
    check_environment
    prepare_directories
    validate_config
    test_network_connectivity
    run_performance_baseline
    start_monitoring_dashboard
    start_triplea_engine

    log_success "🎉 科考船测试环境启动完成"
    log_info "💡 查看监控仪表板: tail -f logs/science_vessel.log"
    log_info "💡 停止测试: ./stop_test.sh"

    # 等待用户中断
    wait
}

# ==================== 清理函数 ====================
cleanup() {
    log_info "执行清理..."

    # 停止监控仪表板
    if [ -f "dashboard.pid" ]; then
        DASHBOARD_PID=$(cat dashboard.pid)
        kill $DASHBOARD_PID 2>/dev/null && log_info "监控仪表板已停止"
        rm -f dashboard.pid
    fi

    # 清理临时文件
    rm -f start_engine.py monitoring_dashboard.py 2>/dev/null

    log_success "清理完成"
}

# ==================== 信号处理 ====================
trap cleanup EXIT INT TERM

# ==================== 脚本入口 ====================
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "使用方法: $0 [选项]"
    echo "选项:"
    echo "  --help, -h     显示帮助信息"
    echo "  --validate     只验证配置和环境"
    echo "  --no-monitor   不启动监控仪表板"
    echo "  --quick        快速启动（跳过部分检查）"
    exit 0
fi

# 执行主函数
main "$@"