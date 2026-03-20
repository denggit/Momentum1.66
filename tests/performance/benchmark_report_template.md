# 四号引擎v3.0性能基准测试报告

## 测试概况

| 项目 | 值 |
|------|-----|
| 测试时间 | `{{timestamp}}` |
| 测试环境 | `{{environment}}` |
| Python版本 | `{{python_version}}` |
| 测试持续时间 | `{{duration_seconds}}` 秒 |

## 系统配置

| 配置项 | 值 |
|--------|-----|
| 操作系统 | `{{os_info}}` |
| CPU物理核心 | `{{cpu_physical_cores}}` |
| CPU逻辑核心 | `{{cpu_logical_cores}}` |
| 内存总量 | `{{total_memory_gb}}` GB |
| CPU频率 | `{{cpu_freq_current}}` MHz |

## 性能目标

| 指标 | 目标值 | 实际值 | 是否达标 |
|------|--------|--------|----------|
| 单Tick处理延迟 | < 1.0 ms | `{{tick_latency_mean}}` ms | `{{tick_latency_met}}` |
| RangeBar生成延迟 | < 0.1 ms | `{{rangebar_latency_mean}}` ms | `{{rangebar_latency_met}}` |
| CVD计算延迟 | < 0.2 ms | `{{cvd_latency_mean}}` ms | `{{cvd_latency_met}}` |
| KDE计算延迟 | < 0.5 ms | `{{kde_latency_mean}}` ms | `{{kde_latency_met}}` |
| 状态机转换延迟 | < 0.1 ms | `{{state_machine_latency_mean}}` ms | `{{state_machine_latency_met}}` |
| 内存使用 | < 1.0 GB | `{{memory_usage_max}}` GB | `{{memory_usage_met}}` |
| CPU使用率 | < 80% | `{{cpu_usage_max}}` % | `{{cpu_usage_met}}` |

## 详细性能数据

### 1. Tick处理延迟

```json
{
  "total_ticks": {{tick_total_ticks}},
  "mean_latency_ms": {{tick_mean_latency}},
  "median_latency_ms": {{tick_median_latency}},
  "p90_latency_ms": {{tick_p90_latency}},
  "p95_latency_ms": {{tick_p95_latency}},
  "p99_latency_ms": {{tick_p99_latency}},
  "min_latency_ms": {{tick_min_latency}},
  "max_latency_ms": {{tick_max_latency}},
  "std_latency_ms": {{tick_std_latency}}
}
```

### 2. 内存使用分析

```json
{
  "duration_seconds": {{memory_duration_seconds}},
  "samples_count": {{memory_samples_count}},
  "memory_rss_avg_mb": {{memory_rss_avg_mb}},
  "memory_rss_min_mb": {{memory_rss_min_mb}},
  "memory_rss_max_mb": {{memory_rss_max_mb}},
  "memory_rss_std_mb": {{memory_rss_std_mb}},
  "memory_growth_rate_mb_per_min": {{memory_growth_rate_mb_per_min}},
  "leak_detected": {{memory_leak_detected}},
  "leak_reason": "{{memory_leak_reason}}"
}
```

### 3. CPU亲和性测试

```json
{
  "dual_core_isolation_supported": {{cpu_affinity_supported}},
  "isolation_quality": "{{cpu_isolation_quality}}",
  "core0_performance": {{cpu_core0_performance}},
  "core1_performance": {{cpu_core1_performance}},
  "interference_analysis": {{cpu_interference_analysis}},
  "process_creation_overhead_ms": {{cpu_process_creation_overhead}}
}
```

### 4. 进程池性能

```json
{
  "process_pool_creation_time_ms": {{process_pool_creation_time}},
  "task_submission_latency_ms": {{task_submission_latency}},
  "worker_utilization": {{worker_utilization}},
  "queue_wait_time_ms": {{queue_wait_time}}
}
```

## 性能图表

### 延迟分布图
![延迟分布图]({{latency_distribution_chart}})

### 内存使用趋势图
![内存使用趋势图]({{memory_usage_chart}})

### CPU使用热图
![CPU使用热图]({{cpu_usage_heatmap}})

## 关键发现

### ✅ 优势
1. **{{advantage1}}**
2. **{{advantage2}}**
3. **{{advantage3}}**

### ⚠️ 待优化项
1. **{{optimization1}}**
2. **{{optimization2}}**
3. **{{optimization3}}**

### ❌ 问题
1. **{{issue1}}**
2. **{{issue2}}**
3. **{{issue3}}**

## 架构建议

### 1. CPU亲和性配置
```
主进程CPU亲和性: 核心 {{main_process_core}}
Worker进程CPU亲和性: 核心 {{worker_process_core}}
进程池大小: {{process_pool_size}} 个Worker
```

### 2. 内存管理策略
```
预分配缓冲区大小: {{buffer_size}} MB
垃圾回收策略: {{gc_strategy}}
内存监控间隔: {{memory_monitoring_interval}} 秒
```

### 3. 性能优化参数
```
Numba JIT缓存: {{numba_cache_enabled}}
矩阵广播优化: {{matrix_broadcasting_enabled}}
进程间通信优化: {{ipc_optimization}}
```

## 测试环境验证

### 阿里云2C2G东京服务器适配性
| 检查项 | 结果 | 说明 |
|--------|------|------|
| CPU核心数验证 | `{{aliyun_cpu_check}}` | {{aliyun_cpu_check_desc}} |
| 内存容量验证 | `{{aliyun_memory_check}}` | {{aliyun_memory_check_desc}} |
| 网络延迟验证 | `{{aliyun_network_check}}` | {{aliyun_network_check_desc}} |
| 磁盘IO验证 | `{{aliyun_disk_check}}` | {{aliyun_disk_check_desc}} |

## 下一步行动

### 短期优化（1周内）
1. **{{short_term_action1}}**
2. **{{short_term_action2}}**
3. **{{short_term_action3}}**

### 中期优化（2-4周）
1. **{{mid_term_action1}}**
2. **{{mid_term_action2}}**
3. **{{mid_term_action3}}**

### 长期优化（1-2月）
1. **{{long_term_action1}}**
2. **{{long_term_action2}}**
3. **{{long_term_action3}}**

## 测试配置

### 测试参数
```yaml
tick_latency_test:
  num_ticks: {{test_num_ticks}}
  warmup_ticks: {{test_warmup_ticks}}
  tick_interval_ms: {{test_tick_interval}}

memory_usage_test:
  duration_seconds: {{test_memory_duration}}
  sampling_interval: {{test_memory_sampling_interval}}

cpu_affinity_test:
  core0: {{test_core0}}
  core1: {{test_core1}}
  test_duration_seconds: {{test_cpu_duration}}
```

### 硬件配置
```yaml
server:
  type: "{{server_type}}"
  cpu_cores: {{server_cpu_cores}}
  memory_gb: {{server_memory_gb}}
  storage_gb: {{server_storage_gb}}
  network_bandwidth: "{{server_network_bandwidth}}"

environment:
  os: "{{environment_os}}"
  python: "{{environment_python}}"
  numba: "{{environment_numba}}"
  numpy: "{{environment_numpy}}"
```

## 结论

**总体评估：** {{overall_assessment}}

**建议：** {{recommendation}}

**风险：** {{risk_assessment}}

---

*报告生成时间：{{report_generation_time}}*
*测试工具版本：v3.0.0*
*报告ID：{{report_id}}*