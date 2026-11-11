# 水质监测数据智能分析管线（WQA-Pipeline）

面向地表水监测场景的数据处理与分析系统，采用**管道-过滤器（Pipe-Filter）**体系结构实现。
系统把"原始监测数据 → 清洗 → 评价 → 预警 → 报表"这条业务链路拆成 7 个相互独立、可单独替换的处理阶段，
阶段之间只通过一条数据通道通信。

---

## 一、体系结构

### 1.1 数据流

```
           ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
 原始 CSV ─▶│ ① 数据加载 │──▶│ ② 数据校验 │──▶│ ③ 缺失值填补│──▶│ ④ 异常值处理│──┐
           └──────────┘   └──────────┘   └──────────┘   └──────────┘  │
                                                                      ▼
           ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  报告/图表 ◀│ ⑦ 报表生成 │◀──│ ⑥ 趋势预警 │◀──│ ⑤ 水质评价 │◀──────────┘
           └──────────┘   └──────────┘   └──────────┘
```

每个阶段都是一个独立的**过滤器**，只依赖一个输入、产出一个输出，
不调用其他过滤器、不感知上下级的存在。数据在管道中单向流动，不存在回环。

### 1.2 为什么选择管道-过滤器

| 判断依据 | 说明 |
| --- | --- |
| 业务形态匹配 | 监测数据处理天然是"逐级加工"的过程：先校验再填补、先清洗再评价，阶段之间存在严格先后依赖 |
| 可修改性 | 更换填补算法、增删一级处理，只需改配置或替换单个文件，不影响其他六段 |
| 可测试性 | 每级过滤器都能用固定的输入独立验证，不需要启动整条管线 |
| 可复用性 | 同一个"缺失值填补"过滤器可被其他水质分析管线直接复用 |
| 对比其他风格 | 分层架构会把清洗与评价混在业务层内，边界不如管道清晰；微服务架构为了 7 个纯函数的处理阶段引入网络通信与独立部署，运维复杂度与收益严重不匹配 |

### 1.3 代码与架构图的对应关系

架构图上每一个方块，在代码中都是一个独立文件、一个独立类：

| 架构图节点 | 代码文件 | 类名 | 职责 |
| --- | --- | --- | --- |
| ① 数据加载 | `filters/f1_loader.py` | `LoaderFilter` | 多文件 CSV 读取与合并 |
| ② 数据校验 | `filters/f2_validate.py` | `ValidateFilter` | 类型、时间、重复、物理量程校验 |
| ③ 缺失值填补 | `filters/f3_missing.py` | `MissingValueFilter` | 分组线性插值 / 均值 / 中位数 / 删除 |
| ④ 异常值处理 | `filters/f4_outlier.py` | `OutlierFilter` | IQR 识别，区分采集异常与污染事件 |
| ⑤ 水质评价 | `filters/f5_index.py` | `IndexFilter` | 单因子评价 + 内梅罗指数 |
| ⑥ 趋势预警 | `filters/f6_trend.py` | `TrendFilter` | 时序斜率、连续超标告警、站点排名 |
| ⑦ 报表生成 | `filters/f7_report.py` | `ReportFilter` | JSON / Markdown 报表与图表 |
| 管道本体 | `pipeline/engine.py` | `Pipeline` | 阶段调度、逐级计时、异常隔离 |
| 数据载体 | `pipeline/base.py` | `FilterContext` | 阶段间唯一流通对象 |

`filters/` 目录的结构与架构图完全同构，新增一个处理阶段就是在图上增加一个方块。

---

## 二、目录结构

```
wqa-pipeline/
├── cli.py                      # 命令行入口（编排层）
├── web.py                      # 结果看板（标准库 http.server，零额外依赖）
├── config/
│   ├── pipeline.yaml           # 主管线编排：七个阶段的顺序与参数
│   ├── pipeline_minimal.yaml   # 精简链路：四级编排，用于对比演示
│   └── water_quality_standard.yaml  # 评价标准限值（可整体替换）
├── pipeline/
│   ├── base.py                 # FilterContext 数据载体 + Filter 抽象基类
│   ├── engine.py               # 管线引擎：注册表、调度、计时、异常隔离
│   ├── standards.py            # 标准加载与评价计算（纯函数）
│   └── charts.py               # 图表渲染
├── filters/                    # ★ 扩展点：一个文件 = 架构图上一个方块
│   ├── f1_loader.py … f7_report.py
│   └── _template.py            # 新过滤器模板（下划线开头，不自动加载）
├── tools/gen_data.py           # 模拟监测数据生成
├── tests/test_pipeline.py      # 单元测试与集成测试
├── data/raw/                   # 原始监测数据
└── reports/                    # 输出：报告与图表
```

---

## 三、快速开始

```bash
# 1. 安装依赖（仅两项）
pip install -r requirements.txt

# 2. 生成模拟监测数据（3 断面 × 30 天 × 4 次/日）
python tools/gen_data.py

# 3. 运行整条管线
python cli.py

# 4. 打开结果看板
python web.py --port 8000
```

运行完成后在 `reports/` 下得到 `report.md`、`report.json` 与四张图表。
详细的启动步骤与演示用例见《运行说明.md》。

---

## 四、配置驱动

管线的行为由 `config/pipeline.yaml` 决定，**改配置不需要改代码**：

```yaml
stages:
  - name: missing
    config:
      strategy: linear      # linear / mean / median / drop
      max_fill_ratio: 0.5
  - name: outlier
    config:
      method: iqr           # sigma / iqr
      k: 3.0
      action: clip          # clip / tag / drop
      corroboration: 2      # 多指标共现判定阈值
```

可对比的两组实证：

| 操作 | 效果 |
| --- | --- |
| `python cli.py --config config/pipeline_minimal.yaml` | 七级链路裁剪为四级（去掉填补、异常、趋势），用于验证配置编排；结果不能视为与完整链同等可靠 |
| 替换 `config/water_quality_standard.yaml` | 可调整已有指标限值；切换评价方案须核对指标和方法，必要时修改代码并重新验证 |

---

## 五、扩展方式

新增一个处理阶段只需三步：

1. 复制 `filters/_template.py` 为 `filters/f8_xxx.py`，改 `name` / `label`，实现 `process()`；
2. 在 `config/pipeline.yaml` 的 `stages` 里按需插入一行；
3. 重新运行，引擎在启动时自动扫描并注册。

不需要修改 `engine.py`，也不需要改动任何已有过滤器——这是"对扩展开放、对修改关闭"的具体落实。

---

## 六、过滤器之间的数据契约

所有过滤器共享同一个 `FilterContext`：

| 字段 | 用途 |
| --- | --- |
| `records` | 主数据，逐条记录的字典列表，全链路唯一被加工的对象 |
| `meta` | 管线级元信息：数据来源、行数统计、配置回执 |
| `artifacts` | 中间产物：类别分布、趋势、告警、图表路径 |
| `events` | 运行事件日志，按过滤器记录"发生了什么" |

过滤器只读写自己负责的字段，因此上游替换不会破坏下游。

---

## 七、质量属性在代码中的落点

| 质量属性 | 实现位置 | 体现方式 |
| --- | --- | --- |
| 可用性 | `pipeline/engine.py` | `on_error=skip` 单级故障隔离，失败阶段标记后继续下游 |
| 性能 | `pipeline/engine.py` | 逐级 `perf_counter` 计时，产出可直接对比的耗时轨迹 |
| 可修改性 | `config/pipeline.yaml`、`filters/` | 增删阶段改配置；替换算法改单文件 |
| 可测试性 | `tests/test_pipeline.py` | 过滤器无隐式依赖，可脱离管线独立断言 |
| 安全性 | `filters/f2_validate.py` | 字段白名单、物理量程边界、非法时间拒绝入库 |
| 易用性 | `cli.py`、`web.py` | 一条命令运行；看板实时读取报告，重跑后刷新即可 |

---

## 八、测试

```bash
python -m unittest discover -s tests -v
```

覆盖：校验过滤器的重复/脏时间/越界处理、线性插值正确性、异常值共现判定、
标准分类边界、引擎的 skip 与 abort 两种隔离策略、七级链路端到端集成。
