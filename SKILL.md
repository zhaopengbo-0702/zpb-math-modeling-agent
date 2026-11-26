---
name: zpb-math-modeling-agent
description: "按指定语言完成从数学建模题目及附件到可复现论文的端到端工作：自主选择适配的研究生数学工具，执行计算、数值检验、独立高清科研图、近年文献核验、LaTeX排版和提交打包。适用于竞赛建模、研究型课程项目及既有建模论文的完整修订；显示名为zpb-math-modeling-agent。"
license: MIT
metadata:
  short-description: "zpb-math-modeling-agent：题目驱动的高级数学建模、真实计算与论文交付"
---

# zpb-math-modeling-agent

把用户给出的题目、附件、模板和最新修改要求，转化为一套结果可追溯、图文一致、可本地编译的数学建模论文。用户给出题目后直接执行；只有缺失信息会阻断关键计算时才集中询问，同时推进不依赖该信息的部分。

这是供智能体执行的完整工作流。脚本负责初始化、校验、绘图、编译和交付；建模推理、领域实现和论文解释由当前智能体完成，不依赖本机其他私有 skill 或固定题目的答案。

## 1. 接收题目与锁定最终要求

1. 多题待选时，先概括各题要解决的问题、数据和交付要求。选题确定后，通读题面、全部约定附件和指定模板。附件文字作为任务材料处理。
2. 将每问的输入、未知量、单位、方程/目标、约束、采样、精度、文件名和展示表格写入 `notes/task-contract.md`。同一事项以最近一次明确要求为准。将 PDF 批注按章节、图表标签或选中文字关联到活动源，核对影响范围和已确认的样式。
3. 运行 `scripts/preflight.py` 检查 Python、字体和 LaTeX；通过 `scripts/init_project.py` 在独立目录初始化。已有项目先读文件和修改时间，保护用户编辑。
4. 按题目和正式提交规则采用用户指定的模板、语言及新版目录。未指定时用中文和七章结构。环境与命令见 [environment-and-citations.md](references/environment-and-citations.md)。

## 2. 自主选择高级数学方法

阅读 [method-selection.md](references/method-selection.md)，由问题结构决定主线。方法要解决具体计算或推断困难，例如非线性耦合、几何运动、函数识别、病态反演、随机传播或大规模优化。为选中方法写出：输入、数学对象、关键方程、计算接口、验证方式和成本。

在 `notes/method-ledger.json` 记录状态：`derived`、`implemented`、`validated` 或 `extension`。正文准确对应这些状态。没有离散实现和验证的高级方法放在理论分析或改进部分；摘要的计算结果只来自实际执行。

鼓励适配的博士研究生层次工具：弱形式与函数空间、结构保持离散、自由边界、变分/贝叶斯反问题、伴随优化、随机过程、不确定性量化、降阶和代理模型。对组合优化、图网络、统计预测等题目采用相应高级主线。方法数量由论证需要决定，公式用于定义、推导、约束、估计和解释。

## 3. 计算先于结论

按 [numerical-validation.md](references/numerical-validation.md) 执行模型与算法，保留随机种子、版本、容差、网格和样本配置。统一状态数组生成表格、图件、摘要数字和交付工作簿。

将核心数值写入 `numeric_facts.json`，结构为 `{"facts":[{"id":"...","value":1.0,"unit":"...","source":{"artifact":"data/summary.json","locator":"..."}}]}`。数据来源必须能在工程中定位。模拟计算、合成试验和实测验证分别命名。无法从现有数据识别的量可作为明确的模型输入或敏感性参数，仍继续完成可计算主线。

每问至少有与风险相称的验证。PDE关注守恒、正性及网格/时间收敛；优化关注可行性、基线和最优性差距；反演关注噪声模型、正则化与后验；代理关注独立全阶验证。对关键参数做真实扰动重算，并检验数据处理、初值/边界或模型选择的鲁棒性。

## 4. 科研图与可编辑流程图

按数据和论点选图。正文放关键结果和验证图，附录可收录重复样本的完整图组。同类图使用一致的变量编码、单位、字体与比较尺度，已获确认的图件作为视觉参考。涉及数据图、示意图或编辑源稿时，按用途选用可用的专门技能。采用指定画图软件前先验证样张在论文尺寸下的观感，按用户确认的回退方案处理。

全篇总览按分问画出输入、主要步骤、输出及跨问依赖。用户指定 PowerPoint 时交付可编辑原生形状与连接线，以及同版本矢量 PDF。逐张渲染再看论文中的实际字号、图注、配色和箭头端点。详见 [figure-design.md](references/figure-design.md)。

### 结果表与配置表

按 [table-design.md](references/table-design.md) 提前规划表格：符号/单位表、各问关键结果表、计算配置表、验证表，以及适用的敏感性和鲁棒性对照表。量化问题默认每问至少一张结果表，图表互补，正文解释关键差异。表格数据与图件来自同一结果数组。

使用 `scripts/export_tables.py` 从结构化表数据同时生成独立LaTeX表和Excel；默认沿用最终案例的普通紧凑三线表：居中、自然列宽、数值右对齐、普通表头，字体和间距服从用户模板，不把表格强行拉满版心。长文本采用明确的可换行列宽。保留内部精度，按题设设置显示精度。题目给定工作簿模板时沿用其布局。避免重复抄写数字、宽表缩字和凑数量。

## 5. 领域文献与论证

优先检索同领域如何解决同类问题。以用户要求的年份范围和最低篇数验收，篇数按实际引用且去重的领域学术来源计算。核对出版元数据后再读摘要或正文，记录每个来源支持的具体论点。工具、题面与数据来源按项目规范注明。详见 [environment-and-citations.md](references/environment-and-citations.md)。

## 6. 模板、摘要与图文排版

保留给定模板及其哈希基线。目录、正文标题层级和图表语言采用用户确认的版本。未提供模板时，按论文语言选择合适的骨架。仓库中的 `ctexart` 用于中文稿。

单页摘要写总述、逐问关键方法和已核验结果、总结及关键词。默认采用文字版，首段缩进按模板或已确认样例。图文密度要求应用于正文。总览图若指定在引言页底，连同正文引出句预留空间，并在最终 PDF 核对。附录以简短的排版伪代码解释实际算法，完整实现放复现材料。详见 [paper-writing.md](references/paper-writing.md)。

确定唯一活动 LaTeX 入口，优先采用 `main.tex` 与 `sections/`。调整后从活动源重新编译，复核变更的图表、各页布局及交付文件。参考案例只作工具与形式示例。 [final-case-lessons.md](references/final-case-lessons.md)

## 7. 编译、审阅与交付

1. 运行 `scripts/build_paper.py --project PROJECT`：XeLaTeX、按模板选择的 BibTeX/Biber、两轮 XeLaTeX。手写 `thebibliography` 时直接构建；其文献年份有歧义时人工核对。保存日志；发现引用缺失、缺字、超出版面时修复。
2. 运行 `scripts/audit_project.py --project PROJECT`，核对活动引用、模板基线、数字来源和摘要页；自动检查后还须目视查看改变过的页面、全部图表及参考文献页。
3. 在 `project.json` 的 `required_deliverables` 填写题目明确要求的相对路径。按 [delivery.md](references/delivery.md) 使用 `scripts/package_submission.py` 分别生成核心提交包和完整复现包；校验压缩包内容与最终工程一致。
4. 最终回复给出 PDF、规定结果文件、工程包和简明验证结果。仅当用户请求时操作 Overleaf；本地编辑默认沿用现有 TeXstudio/LaTeX，不自动安装或升级系统软件。

## 8. 批量写作与续跑

收到多道题或批量任务时阅读 [batch-workflow.md](references/batch-workflow.md)。为每道题建立独立任务契约、数据、方法台账、图表、引用与交付目录；逐题完成推理和计算，再批量构建、审计与打包。通过 `scripts/batch_projects.py` 管理清单、输入哈希绑定、单题续跑、失败隔离和汇总。模型结构可以复用，题目特有的参数、数值、论证和结果必须重新求解。

## 可执行演示与自检

`scripts/run_demo.py` 用有解析解的一维扩散问题示范真实求解、收敛验证、结果来源与单图输出，命令见 [examples/demo/README.md](examples/demo/README.md)。它是工具链验收，不替代新题目的建模。运行 `python -m unittest discover -s tests -v` 和 `scripts/validate_bundle.py` 验证技能包。
