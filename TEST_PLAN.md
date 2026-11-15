# Smage 回归测试方案（可复用核心套件）

> 目标：不是一次性验收，而是以后每次修改 Smage 后，都能用一条命令快速、重复地发现功能退化和小问题。
>
> 状态：待确认。确认后从 P0 开始。

## 1. 定位与原则

- **回归优先**：这套测试是项目的核心质量基线，随代码一起版本化、一起迭代。
- **快慢分级**：改一行代码跑 `--smoke`（≤2 分钟），日常迭代跑 `--fast`，发版前跑 `--full`。
- **可定位**：每个失败都有稳定的用例 ID、日志行号、截图和单用例复现命令。
- **可扩展**：新增模块或控件如果没测试，套件会主动报警，而不是靠人记。
- **可清理**：成功的临时项目自动删除，失败证据自动归档，不会越跑越乱。
- **安全隔离**：测试只写工作区，绝不写入源码目录、真实项目或用户数据。

## 2. 仓库与工作区

测试代码纳入 git；测试数据与运行产物放在仓库外，可随时删除。

```
E:\smage\
├─ tests\regression\              # 纳入 git
│  ├─ README.md                   # 使用与扩展说明
│  ├─ harness.py                  # offscreen、路径守卫、断言、等待、对话框模拟
│  ├─ run_all.py                  # 唯一入口：分级运行、汇总、退出码
│  ├─ inventory.py                # 从源码提取控件/菜单/信号清单
│  ├─ fixtures_gen.py             # 生成合成 FCS/CSV/Parquet/TIFF/坏文件
│  ├─ cases\
│  │  ├─ t00_static.py            # 环境预检、导入、清单基线
│  │  ├─ t01_core_*.py            # 纯函数
│  │  ├─ t02_data_*.py            # 数据层
│  │  ├─ t03_project_import.py    # 项目与导入
│  │  ├─ t04_shell_*.py           # 外壳：菜单/树/标签/设置/扩展/日志
│  │  ├─ t05_mod_<module>.py      # 21 个模块各一个
│  │  ├─ t06_e2e_*.py             # 端到端场景
│  │  └─ t99_teardown.py          # 退出、线程、日志稳定性
│  ├─ baseline\                   # 小的文本黄金文件
│  │  ├─ inventory.json           # 控件清单基线
│  │  └─ golden_*.json            # 输出结构/统计基线
│  └─ quarantine.txt              # 已知偶发用例
└─ src\smage\...

E:\smage_test\samge_batch_test\   # 不纳入 git，运行工作区
├─ _fixtures\                     # 运行时生成；可选真实数据副本
├─ runs\<run-id>\
│  ├─ results.json
│  ├─ report.md / failures.md / coverage.md
│  ├─ screenshots\
│  └─ failures\<case>\            # 失败用例的项目、日志、截图
├─ cases\                         # 仅保留失败用例的工作项目
└─ report\latest.json / latest.md # 指向最近一次运行
```

路径规则：

- `--repo` 默认自动探测 `E:\smage`；
- `--root` 默认 `E:\smage_test\samge_batch_test`，可用环境变量 `SMAGE_TEST_ROOT` 覆盖；
- 路径守卫拒绝写入 `src\`、`data\`、`.git\`、真实项目目录；
- 所有临时项目在 `--root` 内创建。

## 3. 运行入口

| 命令 | 用途 | 目标耗时 |
|---|---|---|
| `--smoke` | 改完代码后的快速自检：导入、核心纯函数、21 页面开关、关键按钮 | ≤2 分钟 |
| `--fast` | 日常迭代：除慢算法外的全部 | 5–8 分钟 |
| `--full` | 发版前全量：含 UMAP、聚类、图像渲染、E2E | 20–40 分钟（不含外部模型） |
| `--module gating` | 单模块 | 秒级–1 分钟 |
| `--case mod.gating.boolean.and_or_not` | 单用例复现 | 秒级 |
| `--tag slow` / `--tag needs-extension` | 按标签筛选 | — |
| `--changed` | 根据 git diff 选受影响模块 + smoke（best-effort） | — |
| `--show` | 有头模式调试；默认 offscreen | — |
| `--retries N` | 失败重试次数，默认 1 | — |
| `--keep-runs N` | 保留最近 N 次运行，默认 10 | — |
| `--update-baseline` | 明确要求时更新黄金文件 | — |

退回码：`0` 全通过；`1` 有失败；`2` 环境或设施错误。

## 4. 结果契约

每次运行写入 `runs\<run-id>\results.json`，每条用例固定字段：

```json
{
  "case_id": "mod.gating.boolean.and_or_not",
  "layer": "L4",
  "module": "gating",
  "tags": ["fast"],
  "status": "pass|fail|skip|error",
  "duration_ms": 1234,
  "message": "",
  "log_errors": [],
  "artifacts": [],
  "repro": "python tests/regression/run_all.py --case mod.gating.boolean.and_or_not"
}
```

- `failures.md` 只列失败项：`file:line`、日志片段、截图路径、复现命令；
- **日志即断言**：每个用例结束扫描该项目 `log\*.log`，出现 `[ERROR]` 或 `Traceback` 即判失败，即使功能断言通过；
- stdout 默认只输出摘要和失败项，`--verbose` 输出全部。

## 5. 基线与黄金文件

存放在 `tests\regression\baseline\`，只提交小的文本文件。

- `inventory.json`：每个页面的可操作控件清单（按钮、菜单、复选框、下拉框、输入框、树的右键菜单等），纯展示标签不计入；
- `golden_*.json`：导出文件的 schema、行数、关键统计与排序后内容；`gates.json` 结构；mask 形状；热图矩阵；页面初始状态；
- 对比时规范化：忽略时间戳、绝对路径、UUID、目录顺序；浮点使用容差；
- `--update-baseline` 只在明确要求时执行，平时只报告差异。

## 6. 覆盖守卫

`inventory.py` 生成当前控件清单，与 `baseline\inventory.json` 比较：

- **新增模块但没有 `t05_mod_<module>.py` → 用例失败**；
- **新增可操作控件但没有对应用例 → 警告**，列入 `coverage.md`；
- 控件删除或重命名 → 提示更新基线；
- `coverage.md` 输出每个控件的覆盖状态：Covered / N/A / Manual / Missing。

这样以后加功能时，套件会主动提示“这个还没测”。

## 7. 稳定性与防误报

- 固定 `PYTHONHASHSEED=0`、随机种子 42、排序遍历；
- 每个用例允许重试 1 次，仍失败才判失败，并标记 `flaky`；
- `quarantine.txt` 记录已知偶发项，输出中单独归类；
- 每个页面开/关 3 次，检查控件销毁、线程回收、无 Qt 警告；
- 所有等待使用条件轮询 + 超时，不使用固定 `sleep`。

## 8. 清理与留存

- 每次运行独立目录；**成功用例的工作项目在汇总后自动删除**；
- **失败用例连同项目、日志、截图复制到 `runs\<id>\failures\<case>\`**；
- `--keep-runs N` 控制历史运行数量（默认 10）；
- `_fixtures`（输入）与 `runs`（输出）分离，整个工作区仍可一键删除。

## 9. 环境预检（t00）

- Python 3.11；PySide6 必须为 **6.8.3**；
- 必需依赖可导入：numpy、pandas、pyarrow、tifffile、flowio、pyqtgraph、scipy；
- 可选依赖缺失（umap-learn、scanpy、peacoqc、igraph、leidenalg、外部扩展）→ `SKIP` 并注明原因；
- 用例 ID 唯一、基线文件存在、fixture 完整；
- 把 Python/PySide6/依赖版本写入每次 `results.json`。

## 10. 测试分层与规模

| 层 | 内容 | 预计检查点 |
|---|---|---|
| L0 静态 | `py_compile`、全模块导入、AST 控件清单、循环导入 | ~60 |
| L1 纯函数 | gates、scale_math、sampling、pipeline、heatmap、image_ops、formatting、feature | ~120 |
| L2 数据层 | columns、files、pool、image_index、outputs、stores、sampling（伪造轻量 host） | ~70 |
| L3 项目与导入 | 创建/打开/保存/另存、UI state、mask 外置/回读；FCS/CSV/Parquet/TIFF | ~60 |
| L4 页面 | 21 个模块冒烟 + 逐控件交互 | ~220 |
| L5 外壳 | 菜单、工作流树、标签页、锁/草稿、设置、扩展、日志、资源指示 | ~70 |
| L6 端到端 | 6–8 条完整流水线 | ~40 |
| L7 稳定性 | 退出不崩溃、线程回收、日志切分、重复开关 | ~20 |

合计约 650 个检查点。

## 11. 模块测试矩阵（21 个）

| 模块 | 核心用例 | 边界 / 小功能 |
|---|---|---|
| **Data Import** | 选择/删除文件、Peek 参数表、Extract/Rename、Apply to、全量/部分抽样、导入队列、进度与取消、重复导入 | Select all、Copy/Paste 参数、图片通道、未知后缀、0 行文件、坏文件隔离、取消后状态 |
| **Gating** | 矩形/椭圆/多边形/象限/分割/范围/偏斜/布尔 AND-OR-NOT/Col2Gate/图像门；绘制-拖动-缩放-删除；树层级；统计 | 越界钳制、零跨度、标签拖动、颜色、handles、复制/粘贴 gate 与 branch、per-file、Unify、级联删除、统计 worker 取消、`gates.json`、锁定时草稿只读 |
| **Scale** | 方法/参数/Min/Max、按列应用、预览、保存、下游传递 | 空列、非法参数、重置、原值/变换值刻度切换 |
| **Compensation** | Set Files、Set Matrix、Copy/Paste、应用、保存 | 单/多文件矩阵、缺列、矩阵格式错误、CytoNorm 扩展缺失 |
| **Unmix** | 矩阵页签、通道匹配、自发荧光、光谱曲线、图片 Unmix、保存、右键 Save as、签名来源（从光谱/手动） | Skip 通道、重名、颜色选择、金字塔输出、无匹配通道、矩阵/相似度热图与曲线图的 PNG/JPG/矢量 PDF 导出、曲线图例随图导出、粘贴矩阵校验（列集合/空值/非数字/重名/列数）、单元格编辑、行拖动排序、两种模式数据独立保存、通道变化后的警告与禁止 unmix |
| **Rename** | 规则增删/排序、预览、应用、保存 | 重名、非法字符、空名、只作用于部分文件 |
| **Subset** | 行过滤条件、列选择、文件选择、图像矩形子集 | 空结果、多条件 AND、按文件过滤、重置、可见文件/列 |
| **Subsample** | 计数依据、分配方式、手动覆盖、seed、覆盖 mask | 0 文件、极小文件、比例边界、图像栅格 |
| **Feature Up** | From/To 文件、Key 选择、Match、特征选择、Calculate | Key 无匹配、背景忽略、按文件缓存、gate 列、applied columns |
| **Gate2Col** | gate→列映射、前缀、背景 0 处理、应用 | 无 gate、列名重复、门标签列、刷新 |
| **Feature** | 变量选择、聚合规则、计数列、背景忽略、Calculate | 自动命名/安全文件名、空规则、signature 失效、缓存 |
| **Heatmap** | 文件/门/变量选择、抽样、Calculate、归一化、行列聚类、显示设置、legend 标题、右键 Save as | NaN 填充、数字文本/颜色、聚类参数、矩阵复制、导出文本、legend 标题随 Normalize/自定义/隐藏、PNG/JPG/矢量 PDF 导出、空热图禁存、新模块不预选文件、跨模块门重名时才加模块名 |
| **Illumination** | 子模块增删、mask 绘制/导入、应用、图像级别 | mask 的 npy 外置/回读、通道切换、尺寸不匹配 |
| **Data Clean** | 模式/参数、时间列、变量选择、Calculate | 无时间列、MAD/IT 边界、去除零值、结果列、取消 |
| **Batch Align** | 批次表、Copy/Paste、变量、参数、Calculate、Report | 单批次、缺参考、扩展缺失、`alignment.npz`、HTML/PDF/PNG/SVG 报告、失败路径 |
| **Dimred** | PCA/UMAP/tSNE、参数、变量、Max events、Normalize/PCA/parallel、Calculate | 列名冲突、样本不足、取消、后端切换 |
| **Cluster** | kmeans/leiden/louvain/phenograph/FlowSOM/PARC、参数、变量、Calculate | 扩展缺失、聚类列重名、栅格显示、取消 |
| **Area** | 文件/列/网格/阈值/信号、Apply、Preview | 空文件、网格=1、低/高阈值、Density 边界、复制参数 |
| **Segmentation** | Cellpose/StarDist/Nuclear Expansion/Mask Input、通道/阈值/面积/半径、Calculate、预览 | 扩展缺失、mask 缓存、mask 列、图像/表格文件判断、取消、尺寸不匹配 |
| **Figure** | Add Scatter/Overlay/Histogram/Image/N×N、卡片拖拽与换行、单/多选设置、Duplicate/Delete/Permute、Overlay 图层、颜色/刻度/图例 | 空页面、框选、缩放/平移、点数上限、锁定时只读、原值刻度、拖拽图层排序 |
| **Export** | 文件/列选择、格式、输出目录、Export/Cancel | 覆盖确认、非法目录、FCS 列名安全化、部分失败、日志 |

## 12. 外壳与微交互清单

- **菜单栏**：New/Open/Save As、Extension Manager、Global Settings、About；
- **工作流树**：展开/折叠持久化、任务颜色、锁图标、状态点、右键菜单、Actions ▸（重命名/删除/锁定）、“+” 添加模块、搜索/分组、Esc 关闭浮层；
- **标签页**：第一个 Workflow 页签不可拖动、其余可排序、关闭按钮、重复打开聚焦；
- **锁与草稿**：Save 置灰/点亮、Draft (locked)、关闭时草稿清理、锁定模块只读、会话内 Unlock；
- **未保存提示**：关闭页面 / 关闭窗口 / 导入运行中禁止关闭（Yes/No 两条分支）；
- **全局设置**：字号、密度、绘图背景、`max_points`、`nxn_max_points`、默认 colormap、记住窗口几何；自定义 colormap 增删改与重名；
- **交互细节**：`_HoverLabel` 提示位置与 `QCursor`、`_NoWheelComboBox/_NoWheelSpinBox` 不抢滚轮、tooltip、光标形状、禁用态；
- **资源与日志**：文件夹大小指示与 `_format_bytes`、日志轮转（20 个/20 MB）、`error.log`、`debug.log`、日志头环境信息；
- **扩展管理**：安装/移除/状态/日志、外部 Python 路径、扩展缺失提示；
- **窗口**：几何记忆开/关、标题栏项目名、关闭清理、退出码与 segfault 检查。

## 13. 测试数据

- **合成数据（默认，可在新机器/CI 复现）**：
  - FCS：用 flowio 的 `create_fcs` 生成多通道、可控分布的事件表；
  - CSV/Parquet：5k 行，覆盖 NaN、负数、常数列、极大值、Unicode 列名、重名列；
  - TIFF：3 通道 64×64、单通道 2D、带 mask；
  - 坏数据：截断 FCS、空 CSV、错误后缀、缺列 Parquet。
- **真实数据（可选）**：从 `E:\smage\data` 复制 4 个 FCS、3 个 sub-TIFF、2 个 OME-TIFF 到 `_fixtures\real\`；对应用例打 `needs-real-data`，缺数据自动 SKIP。
- 固定随机种子；fixture 生成结果记录语义校验（行数/通道/列名），不依赖字节级哈希。

## 14. 自动化方式

- `QT_QPA_PLATFORM=offscreen`，默认不显示窗口；`--show` 用于调试；
- `QTest.mouseClick/keyClick/mouseMove` 模拟交互；画布门用页面函数 + 事件双路径；
- 模态对话框统一 monkeypatch：`QMessageBox`、`QFileDialog`、`QInputDialog`、`QColorDialog`、`QProgressDialog`，记录“弹了哪个框、选了什么”；
- 异步 worker 用条件等待（10–30 秒超时）；
- 断言同时检查**内存状态 + 磁盘产物**：Parquet 行列数、`gates.json`、`mask.npy`、导出文件、`project.json`；
- 每个脚本独立子进程，退出段 segfault 不会拖垮整套；
- 截图用 `widget.grab()` 存 `runs\<id>\screenshots\`。

## 15. 报告与缺陷处理

每次运行生成：

- `results.json`：机器可读；
- `report.md`：按层/模块汇总，通过率、耗时、失败列表；
- `failures.md`：失败项细节与复现命令；
- `coverage.md`：控件覆盖状态；
- `feature_inventory.md`：源码控件清单及测试映射；
- `bugs.md`：缺陷清单（Severity: Blocker / Major / Minor，复现步骤、期望/实际、日志行号）。

处理策略（已确认）：

- **只报告、不修改**；只有阻塞后续用例的问题才修，并在报告中注明；
- 外部扩展：检测现有环境，有则跑最小真实用例，无则只测错误路径/模拟；不自动安装、不下载模型。

## 16. 端到端场景

1. 多文件 FCS 导入 → Gating（矩形 + 布尔）→ Scale → Export（CSV/FCS）。
2. 图像导入 → 矩形 Subset → Segmentation（Nuclear Expansion）→ Feature → Gate2Col → Heatmap。
3. 导入 → Compensation → Unmix → Rename → Feature → Figure。
4. 导入 → Subsample → Dimred（PCA）→ Cluster（kmeans）→ Area。
5. 导入 → Data Clean → Batch Align → Feature Up（扩展缺失时 SKIP）。
6. Figure：N×N + Overlay + 图层排序 + 导出。
7. 项目往返：创建 → 保存 → 重新加载 → 状态一致。
8. 锁/草稿：保存上游 → 打开下游 → 修改 → 关闭不保存 → 重新打开恢复。

## 17. 阶段计划

| 阶段 | 内容 | 产出 |
|---|---|---|
| P0 | harness、路径守卫、fixtures 生成、控件清单、smoke 骨架 | 可运行骨架 |
| P1 | L0–L3：静态、纯函数、数据层、项目/导入 | 第一批结果 |
| P2 | L4 冒烟 + L5 外壳 | 页面与外壳结果 |
| P3 | 21 个模块逐个深测（分 5–6 批） | 模块结果 + 缺陷 |
| P4 | L6 端到端 | 场景结果 |
| P5 | L7 稳定性、报告、基线冻结、README、（可选）CI | 完整报告 |

每阶段结束输出小结，确认后继续。

## 18. 已确认的默认项

- 外部扩展：有环境则真跑最小用例；否则模拟/错误路径；不自动安装、不下载模型；
- 发现 bug：只报告；阻塞性问题修复并注明；
- 报告语言：中文说明 + 英文用例名/字段名；
- 测试脚本：纳入 `E:\smage\tests\regression\`，受 git 管理；
- 测试项目与产物：放 `E:\smage_test\samge_batch_test\`，可随时删除；
- 旧测试脚本 `E:\smage\data\test_*.py` 暂不动；新套件稳定后提供归档/清理清单，确认后处理。

## 19. 执行前提

当前会话的 Auto 模式不可用，Bash/PowerShell/诊断工具被拦截。开始 P0 前需要切换到允许执行 Python 的权限模式，否则只能生成代码、无法运行测试。
