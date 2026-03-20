#!/bin/bash
# 四号引擎KDE+Range Bar算法v3.0高性能优化版开发环境设置脚本
# 双核隔离架构 + Numba JIT编译 + Numpy矩阵广播

set -e

echo "🚀 开始设置四号引擎v3.0高性能开发环境..."

# 检查Python版本
echo "📋 检查Python版本..."
python3 --version
python3 -c "import sys; assert sys.version_info >= (3, 8), 'Python 3.8+ required'"

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "🔄 创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
echo "🔧 激活虚拟环境..."
source venv/bin/activate

# 升级pip
echo "⬆️ 升级pip..."
pip install --upgrade pip

# 安装基础依赖
echo "📦 安装基础依赖..."
pip install numpy>=1.21.0
pip install numba>=0.58.0
pip install psutil>=5.9.0
pip install scipy>=1.7.0
pip install aiohttp>=3.8.0
pip install pytest>=7.0.0
pip install pytest-asyncio>=0.21.0

# 安装项目其他依赖
echo "📦 安装项目其他依赖..."
pip install -r requirements.txt

# 创建Numba缓存目录
echo "🗂️ 创建Numba缓存目录..."
mkdir -p ~/.cache/numba
chmod 755 ~/.cache/numba

# 验证安装
echo "✅ 验证安装..."
python3 -c "import numpy as np; print(f'NumPy版本: {np.__version__}')"
python3 -c "import numba; print(f'Numba版本: {numba.__version__}')"
python3 -c "import psutil; print(f'psutil版本: {psutil.__version__}')"

# 运行Numba JIT编译测试
echo "⚡ 运行Numba JIT编译测试..."
python3 -c "
import numpy as np
from numba import njit, vectorize
import time

@njit(cache=True)
def sum_array(arr):
    result = 0.0
    for i in range(len(arr)):
        result += arr[i]
    return result

arr = np.random.random(1000000)
start = time.time()
result = sum_array(arr)
elapsed = (time.time() - start) * 1000
print(f'✅ Numba JIT编译测试通过! 计算100万元素数组求和: {elapsed:.2f}ms')
"

echo "🎉 开发环境设置完成！"
echo "👉 使用以下命令激活虚拟环境: source venv/bin/activate"
echo "👉 运行性能基准测试: python -m tests.performance.test_tick_latency"