# Triple-A 四号引擎

基于 Fabio Valentini 的 Triple-A 模型（吸收、累积、侵略）的量化交易策略引擎，专注于剥头皮和日内交易。

## 架构设计

复用三号引擎的单向数据流架构：

```
Tick数据 → MarketContext → TripleADetector → 信号验证 → TripleAExecutor → 科考船记录
```

### 核心组件

1. **TripleADetector** - Triple-A 模型检测器
   - Absorption（吸收）检测：机构在关键水平吸收流动性
   - Accumulation（累积）检测：机构在吸收后累积头寸
   - Aggression（侵略）检测：机构发动攻击，推动价格突破
   - Failed Auction（失败拍卖）检测：突破失败，价格回归累积区间

2. **TripleAExecutor** - 专用执行器
   - 风险管理：基于风险比例计算仓位大小
   - 止损止盈：紧止损，高胜率，追求爆发性动量
   - Failed Auction 处理：立即止损，可选反手交易

3. **TripleACSVTracker** - 科考船系统
   - 信号记录：记录所有 Triple-A 信号维度
   - 参数验证：支持参数网格搜索和 walk-forward 优化
   - 性能评估：实时计算胜率、盈亏比、夏普比率

4. **TripleAOrchestrator** - 编排器
   - 协调所有组件，管理数据流
   - 支持 collect（收集）和 live（实盘）模式

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑配置文件 `config/triple_a/ETH-USDT-SWAP.yaml`，调整参数：

```yaml
# 关键参数
absorption_price_threshold: 0.001      # 吸收价格阈值 0.1%
absorption_volume_ratio: 2.0           # 成交量比率
accumulation_width_pct: 0.003          # 累积区间宽度 0.3%
aggression_volume_spike: 3.0           # 成交量爆发倍数
failed_auction_detection_threshold: 0.65 # 失败拍卖检测阈值
```

### 3. 运行引擎

#### 收集模式（科考船模式）
只收集数据和信号，不执行实盘，发送邮件警报：

```bash
cd engines/engine_4_triple_a
python strategy.py --symbol ETH-USDT-SWAP --mode collect
```

#### 实盘模式
自动执行交易（请谨慎使用）：

```bash
cd engines/engine_4_triple_a
python strategy.py --symbol ETH-USDT-SWAP --mode live
```

### 4. 查看结果

- 信号记录：`data/triple_a_research/signals_ETH-USDT-SWAP.csv`
- 交易记录：`data/triple_a_research/trades_ETH-USDT-SWAP.csv`
- 参数实验：`data/triple_a_research/parameters_ETH-USDT-SWAP.csv`

## Triple-A 模型详解

### Absorption（吸收）
- **特征**：价格在关键水平（支撑/阻力、POC、VAH/VAL）±0.1%范围内波动
- **订单流**：出现异常大单（>平均成交量10倍）但价格未突破
- **时间**：持续至少30秒

### Accumulation（累积）
- **特征**：价格在窄幅区间整理（振幅<0.3%）
- **成交量**：成交量逐渐萎缩，为平均成交量的30-70%
- **订单块**：价格多次测试同一水平但未突破

### Aggression（侵略）
- **特征**：成交量突然放大3倍以上
- **突破**：价格突破累积区间边界 ±0.2%
- **速度**：价格变化速度突然增加

### Failed Auction（失败拍卖）
- **特征**：Aggression 触发后5分钟内，价格重新进入 Accumulation 区间
- **处理**：立即止损，防止小亏变大亏
- **反手**：可选反手交易，捕捉反转机会

## 风险管理

### 仓位计算
基于风险比例和杠杆的动态仓位计算：

```python
风险金额 = 账户余额 × 风险比例（默认0.3%）
价格风险 = |入场价 - 止损价|
每张合约风险 = 价格风险 × 合约面值
合约数量 = min(风险金额 / 每张合约风险, 保证金限额)
```

### 风险控制
- 单笔风险：≤ 账户余额的0.3%
- 日风险限制：≤ 账户余额的2%
- 最大持仓数量：3个同时持仓
- 最大连续亏损熔断：5次

## 科考船研究系统

### 数据收集模式
- **纯收集**：只记录信号，不模拟交易
- **模拟交易**：记录信号并模拟交易，计算绩效
- **参数实验**：自动测试不同参数组合

### 参数验证
支持网格搜索和 walk-forward 优化：

```yaml
parameter_experiments:
  - param: "absorption_volume_ratio"
    values: [5, 8, 10, 12, 15, 20]
    metric: "sharpe_ratio"
  - param: "failed_auction_detection_threshold"
    values: [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]
    metric: "win_rate"
```

### 性能指标
- 胜率（目标 >60%）
- 盈亏比（目标 >1.8）
- 夏普比率（目标 >1.5）
- 最大回撤（目标 <10%）
- 卡尔玛比率（目标 >3.0）

## 开发路线图

### 已完成
- [x] 基础架构搭建（复用三号引擎单向数据流）
- [x] Triple-A 检测器核心逻辑
- [x] 配置系统和科考船框架
- [x] 执行器和风险管理

### 进行中
- [ ] 精细订单流分析（足迹图、L2数据）
- [ ] 流动性狩猎识别
- [ ] 价值区间分析（VAH/VAL/POC）
- [ ] 参数优化和回测框架

### 计划中
- [ ] 自适应参数调整
- [ ] 多时间框架验证
- [ ] 高级可视化工具
- [ ] 机器学习优化

## 文件结构

```
engines/engine_4_triple_a/
├── strategy.py              # 引擎入口
├── README.md               # 本文档
└── __init__.py

src/strategy/triple_a/
├── __init__.py
├── config.py               # Triple-A 配置类
├── detector.py             # Triple-A 检测器核心
├── tracker.py              # 科考船追踪器
├── orderflow_analyzer.py   # 精细订单流分析（待实现）
├── liquidity_hunter.py     # 流动性狩猎识别（待实现）
└── value_area.py           # 价值区间分析（待实现）

src/execution/
├── triple_a_executor.py    # Triple-A 专用执行器
└── adaptive_lifecycle_manager.py  # 自适应生命周期管理（待实现）

config/triple_a/
└── ETH-USDT-SWAP.yaml     # 示例配置文件
```

## 注意事项

1. **实盘风险**：请在模拟盘充分测试后再使用实盘模式
2. **数据质量**：需要 tick 级和 L2 订单簿数据以获得最佳效果
3. **市场环境**：策略在不同市场环境下表现可能不同
4. **参数优化**：定期使用科考船系统优化参数

## 故障排除

### 常见问题

1. **导入错误**：确保项目根目录在 Python 路径中
2. **配置加载失败**：检查 `config/triple_a/` 目录下的 YAML 文件
3. **数据连接失败**：检查网络连接和 OKX API 密钥
4. **检测器不工作**：调整检测阈值参数

### 日志查看

日志文件位于 `logs/` 目录，按日期分割：

```bash
tail -f logs/triple_a_$(date +%Y-%m-%d).log
```

## 贡献

欢迎提交 Issue 和 Pull Request 改进四号引擎。

## 许可证

本项目基于现有 Momentum 1.66 代码库，遵循相同的许可证。