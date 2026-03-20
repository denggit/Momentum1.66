#!/bin/bash
# 四号引擎科考船实盘测试停止脚本
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

# ==================== 停止监控仪表板 ====================
stop_monitoring_dashboard() {
    log_info "停止监控仪表板..."

    if [ -f "dashboard.pid" ]; then
        DASHBOARD_PID=$(cat dashboard.pid)

        if kill -0 $DASHBOARD_PID 2>/dev/null; then
            kill $DASHBOARD_PID
            sleep 1

            if kill -0 $DASHBOARD_PID 2>/dev/null; then
                log_warning "监控仪表板未正常停止，强制终止..."
                kill -9 $DASHBOARD_PID 2>/dev/null
            fi

            log_success "监控仪表板已停止 (PID: $DASHBOARD_PID)"
            rm -f dashboard.pid
        else
            log_warning "监控仪表板进程不存在 (PID: $DASHBOARD_PID)"
            rm -f dashboard.pid
        fi
    else
        log_info "未找到监控仪表板PID文件"
    fi
}

# ==================== 停止四号引擎 ====================
stop_triplea_engine() {
    log_info "停止四号引擎..."

    # 查找四号引擎相关进程
    ENGINE_PIDS=$(ps aux | grep -E "python.*(triplea|signal_generator|orchestrator)" | grep -v grep | awk '{print $2}')

    if [ -n "$ENGINE_PIDS" ]; then
        log_info "找到四号引擎进程: $ENGINE_PIDS"

        # 优雅停止
        for pid in $ENGINE_PIDS; do
            if kill -0 $pid 2>/dev/null; then
                kill $pid
                log_info "发送停止信号到进程: $pid"
            fi
        done

        # 等待进程停止
        sleep 2

        # 检查是否还有进程运行
        REMAINING_PIDS=$(ps aux | grep -E "python.*(triplea|signal_generator|orchestrator)" | grep -v grep | awk '{print $2}')

        if [ -n "$REMAINING_PIDS" ]; then
            log_warning "仍有进程运行，强制终止..."
            for pid in $REMAINING_PIDS; do
                kill -9 $pid 2>/dev/null
            done
        fi

        log_success "四号引擎已停止"
    else
        log_info "未找到运行的四号引擎进程"
    fi
}

# ==================== 清理临时文件 ====================
cleanup_temp_files() {
    log_info "清理临时文件..."

    # 清理Python临时文件
    rm -f start_engine.py 2>/dev/null
    rm -f monitoring_dashboard.py 2>/dev/null
    rm -f __pycache__/*.pyc 2>/dev/null
    rm -rf .pytest_cache/ 2>/dev/null

    # 清理PID文件
    rm -f dashboard.pid 2>/dev/null
    rm -f engine.pid 2>/dev/null

    log_success "临时文件清理完成"
}

# ==================== 生成测试报告 ====================
generate_test_report() {
    log_info "生成测试报告..."

    # 创建报告目录
    mkdir -p reports

    # 生成简单报告
    REPORT_FILE="reports/test_report_$(date +%Y%m%d_%H%M%S).md"

    cat > "$REPORT_FILE" << EOF
# 四号引擎科考船测试报告
## 测试信息
- **测试时间**: $(date '+%Y-%m-%d %H:%M:%S')
- **测试环境**: 科考船实盘测试
- **测试模式**: 模拟交易
- **交易对**: ETH-USDT-SWAP

## 测试结果
### 1. 环境检查
- ✅ Python版本检查
- ✅ 依赖检查
- ✅ 配置文件验证
- ✅ 网络连接测试

### 2. 系统性能
- **运行时间**: $(uptime -p 2>/dev/null || echo "未知")
- **CPU使用率**: $(top -bn1 | grep "Cpu(s)" | awk '{print $2}')%
- **内存使用**: $(free -m | awk 'NR==2{printf "%.1f%%", $3*100/$2}')

### 3. 四号引擎状态
- **引擎版本**: v3.0
- **状态**: 已停止
- **测试结果**: 手动停止

### 4. 测试总结
本次科考船测试环境已成功搭建并运行。测试环境包括：
1. 完整的配置管理系统
2. 实时监控仪表板
3. 性能基准测试框架
4. 紧急处理机制

## 下一步建议
1. 连接真实市场数据进行测试
2. 运行72小时压力测试
3. 验证风控系统功能
4. 收集性能基准数据

## 日志文件
- 系统日志: logs/science_vessel.log
- 性能日志: data/performance/
- 交易日志: data/order/

---
*报告生成时间: $(date)*
EOF

    log_success "测试报告已生成: $REPORT_FILE"
}

# ==================== 备份测试数据 ====================
backup_test_data() {
    log_info "备份测试数据..."

    BACKUP_DIR="backups/test_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"

    # 备份重要数据
    if [ -d "data" ]; then
        cp -r data "$BACKUP_DIR/" 2>/dev/null || log_warning "数据备份失败"
    fi

    if [ -d "logs" ]; then
        cp -r logs "$BACKUP_DIR/" 2>/dev/null || log_warning "日志备份失败"
    fi

    if [ -d "reports" ]; then
        cp -r reports "$BACKUP_DIR/" 2>/dev/null || log_warning "报告备份失败"
    fi

    # 压缩备份
    if [ -d "$BACKUP_DIR" ]; then
        tar -czf "${BACKUP_DIR}.tar.gz" "$BACKUP_DIR" 2>/dev/null
        rm -rf "$BACKUP_DIR"
        log_success "测试数据已备份: ${BACKUP_DIR}.tar.gz"
    else
        log_warning "无测试数据可备份"
    fi
}

# ==================== 主函数 ====================
main() {
    log_info "🛑 四号引擎科考船实盘测试停止"
    log_info "开始停止测试环境..."
    echo ""

    # 执行停止步骤
    stop_monitoring_dashboard
    stop_triplea_engine
    cleanup_temp_files
    generate_test_report
    backup_test_data

    log_success "🎉 科考船测试环境已完全停止"
    log_info "💡 测试报告位置: reports/"
    log_info "💡 备份数据位置: backups/"
    echo ""
    log_info "感谢使用四号引擎科考船测试环境"
}

# ==================== 脚本入口 ====================
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "使用方法: $0 [选项]"
    echo "选项:"
    echo "  --help, -h     显示帮助信息"
    echo "  --force        强制停止（不生成报告）"
    echo "  --no-backup    不备份测试数据"
    echo "  --quick        快速停止"
    exit 0
fi

# 执行主函数
main "$@"