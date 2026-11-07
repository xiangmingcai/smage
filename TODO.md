# Smage — TODO（发布前要处理）

> 这里记录「现在不做、但正式发布前必须处理」的事情。新发现的遗留问题请追加到对应分组，并在完成后删除或移到「已完成」。

## 模块间提示 / 用户体验

- [ ] **整理所有模块：检查哪些模块在上游出现 Subsample 模块时需要给用户提示。**
  - 例如 Feature Up：若 From（聚合）文件被采样，查表会缺少部分 key，对应的 To 行变成 NA，映射结果会随采样数变化，应提示用户。
  - 例如 Segmentation：它始终使用完整图像（图像被采样后无法做分割），应说明采样对它无效。
  - 例如 Gate2Col：若上游有采样，0/1 图像层只在与主图遮罩一致时才可理解，需要确认提示文案。
  - 同类问题也适用于后续新增的 Dimension Reduction / Cluster 等模块。
- [ ] Subset 隐藏 `x` / `y` 后，表格类 Figure 无法选择坐标轴；确认是否需要提示或做保护。
- [ ] 检查各模块在「上游文件集合发生变化」时的提示是否清楚（文件被隐藏、被采样、被重新计算）。

## 未实现的模块

- [ ] Export（占位）
- [ ] Feature Up / Gate2Col / Subset / Subsample / Area / Data Clean / Batch Alignment 的回归测试（目前靠手工 + 临时脚本验证）

## Batch Alignment（批次校正）

- [ ] CytoNorm 应用时每个细胞要做一次「最近 SOM 节点」计算；表格数据约 1–3 秒且结果会缓存，但超大图像首次读取仍会明显变慢，需要评估是否要按文件预计算并缓存到磁盘。
- [ ] 生成 Report 会把每个文件完整读一遍（再抽样统计），文件多、图像大时耗时较长，目前只有状态栏进度提示。
- [ ] 某个批次没有 Reference 时该批次不做校正（日志里只提示一句）；确认发布前是否要在批次表里显式标红。
- [ ] ComBat 假设数据近似对数正态；界面上已提示「需要 log 尺度请在上游放 Scale」，但还没做校验（例如数值跨度过大时建议先 log）。
- [ ] 报告里的批次差异统计基于每文件抽样（≤ 20,000 行），已在报告中注明；若用户需要精确值，后续可加「全量统计」开关。

## Area（面积）

- [ ] Area 的 Scatter 预览会整列读取 x / y / 信号变量，超大图像会很慢（默认 Auto 对图像用 Image 视图）；后续可做按行降采样。
- [ ] Area 用 parquet 的 min/max 统计确定坐标系；若上游 Scale 改写了 x / y，会回退成整列读取，速度较慢，目前没有提示。
- [ ] 密度阈值是「每个格子的点数」，含义随「Grid cells」变化；确认发布前是否要在界面上说明，或改成按密度比例设置。

## 数据兼容

- [ ] 老项目迁移：Segmentation mask 已改为 parquet + imgidx，Feature Down 输出命名也已变更；旧项目打开后是否需要迁移或给出明确提示。
- [ ] 各模块的「输出签名」字段变更（例如本次加入采样签名）会让旧结果显示为「未计算」，确认这是否符合预期并写入说明。
- [ ] Gating 门树已移到 `Module/<task>/gates.json`，老项目首次打开会惰性迁移（`params.gates` → gates.json，`files` 默认填全部可见文件）；确认迁移提示是否要在界面上给出。

## 圈门（Gating）

- [ ] Figure 的 Filter 下拉仍列出上游所有门（不区分该文件是否在门的文件作用域内），只是越界文件会显示「无法应用」；发布前可改成按当前文件作用域过滤。
- [ ] Gating 未选文件时预览仍显示第一个可用文件、也能画门，但门不作用于任何文件；确认是否需要提示用户先选文件。
- [ ] Gating 的 Image 子集视图按「超过半数像素通过」的块级规则隐藏门外的块（深度放大时按像素精确）；确认这个块级近似是否符合预期。

## 渲染

- [ ] **给 Figure 模块增加「离散类别色」渲染选项**：目前 mask / cluster 这类类别列都按连续数值色渲染；后续让用户能为某一列选择「离散类别色」（每个类别一个颜色，像 FlowJo 的 population 着色）。
- [ ] Subsample 的图像覆盖层只在金字塔层级路径生效；极端放大（直接读原始像素的回退路径）时不做遮罩。评估是否需要在发布前补齐。
- [ ] 大图（>50M 像素）采样掩膜的内存占用需要评估。

## Data Clean（PeacoQC）

- [ ] PeacoQC 需要把整个文件的检验变量读进内存（polars DataFrame）；上千万行的大图像会占用较多内存，评估是否要分批/抽样。
- [ ] 新列是 0/1，但在 Figure 里仍按连续色渲染；离散类别色见上面「渲染」分组。
- [ ] 上游 Subsample 采样时，未采样到的行写 NaN（未评估），采样到的行里缺值写 0；确认发布前是否需要把这个规则写进提示文案。

## 全局设置（Settings → Global Settings…）

- [ ] **Dark 主题**：这次只做了字体大小 / 行密度 / 绘图区背景 / 窗口几何。完整的深色主题需要先把约 200 处内联的 `setStyleSheet("color: #…")` 抽成颜色 token，否则深色下会出现白底浅字。
- [ ] **N×N 图采样点数**（`plot.nxn_max_points`，默认 5000）目前是占位参数，等 Figure 的 N×N 图实现后再接上；未编辑前会自动跟随「Scatter max points ÷ 10」。
- [ ] 删除仍被已保存图表引用的自定义 colormap 时，渲染会静默回退到 viridis；确认是否需要提示。
- [ ] 设置存放在 `%APPDATA%\Smage\settings.json`（与 `extensions.json` 同目录），不随项目走；确认是否需要「随项目覆盖」的能力。
- [ ] 字体大小设置通过 `QApplication.setFont` + 主窗口 `setFont` 生效；少数写死 `font-size` 的内联样式不跟随，后续统一时一并处理。

## 发布准备

- [ ] 打包为 Windows 可执行程序（PyInstaller 等）并验证 `pythonw` 启动方式。
  - [x] 路径已改为经 `smage/frozen.py` 的 `bundle_root()` / `package_root()` 解析（图标、runner 脚本、`sys.path`）。
  - [ ] `--add-data` 打入 `src/smage/icons` → `smage/icons` 和 `src/smage/runners` → `smage/runners`。
  - [ ] 在没装 Python 的干净 Windows 上，从任意目录双击 exe，跑通导入 → 门控 → 导出。
  - [ ] 扩展管理器不应把 `Smage.exe` 当成 Python 解释器（已在 `_detect_python_interpreters` 中过滤 frozen 的 `sys.executable`）。
  - [x] 动态导入已覆盖：`packaging/smage.spec` 对 `smage`、numba、llvmlite、umap、scanpy 做 `collect_submodules`；`smage/selfcheck.py` + `Smage.exe --self-test` 会在冻结包里逐个导入验证。
  - [x] 业务流程验证：回归套件已打进包，`Smage.exe --run-tests [--tag e2e]` 在冻结环境中运行（结果写到 `%TEMP%\SmageTests\frozen-latest.md`）。
  - [ ] 每次改依赖后跑一次 `packaging/build_win.py`，确认冻结版 `--self-test` 退出码为 0。
- [ ] **锁定依赖版本**：`requirements.txt` 目前只锁了 `PySide6==6.8.3`，其余都是浮动版本。
  - [ ] 从干净环境安装后生成 `requirements-lock.txt`（`pip freeze`），构建时改为 `pip install -r requirements-lock.txt`，保证每次打包的依赖、体积和行为一致。
  - [ ] 把构建期依赖（PyInstaller 等）拆到 `requirements-build.txt`，不混进运行期清单。
  - [ ] 升级任何依赖时重新生成锁文件，并在 `--full` 回归套件上验证后再发布。
- [ ] **确定以 GPL-3.0 发布**：
  - [ ] 根目录添加 `LICENSE`（GPL-3.0 全文），README 写明 "Smage is free software under GPL-3.0"。
  - [ ] 发布安装包时必须向用户提供完整对应源码（公开 GitHub 仓库，或随包提供源码包/下载链接）。
  - [ ] 用户协议/EULA 不得附加与 GPL 冲突的限制（禁止再分发、禁止商用等）。
  - [ ] 决定是否接受外部代码贡献；若接受，先确定 CLA / DCO 策略，以保留将来双许可的可能性。
  - [ ] 发布前用 `pip-licenses` / ScanCode 审计全部依赖的许可证（重点排查 GPL-2.0-only 等与 GPL-3.0 不兼容的项，以及 Cellpose/StarDist 模型权重的独立条款），生成 `LICENSES/` 目录随安装包分发。
- [ ] **代码签名**：给 `Smage.exe` 和安装包都签名，避免用户看到 SmartScreen“未知发布者”警告，并降低杀软误报。
  - [ ] 首选申请 **SignPath Foundation**（面向开源项目的免费代码签名）；备选 Azure Trusted Signing 或商业 OV/EV 证书。
  - [ ] 签名流程写进发布构建（先 `Smage.exe`，再 `Setup.exe`），并加可信时间戳。
  - [ ] 不启用 UPX 等压缩/加壳；若仍被误报，向 Microsoft 提交样本申诉。
- [ ] 补齐 README（功能列表、截图、安装说明）。
- [ ] 统一错误提示与日志格式。
