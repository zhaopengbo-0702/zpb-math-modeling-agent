# 来源与第三方依赖

本仓库的技能说明、Python工具、原创通用LaTeX骨架和解析扩散演示按根目录MIT许可证发布。它们根据一次完整建模项目重新组织为可迁移实现。

## 本地技能的集成方式

设计时参考了本机 `skill-creator` 的技能结构约定，以及 `academic-writing`、`academic-citation`、`academic-figure` 和 PDF 工作流所覆盖的任务类型。仓库提供的是重新编写的流程和工具，没有逐份复制这些本地技能的源码或参考手册，也不要求使用者另装它们。

用户题目、附件、指定竞赛模板和字体由使用者在运行时提供。当前原始 `cumcmthesis.cls` / `cumcm2026.sty` 及其他本地技能没有随本仓库再分发。本仓库的 `assets/templates/main.tex` 为独立编写的通用骨架；LaTeX发行版中的宏包继续遵循各自许可。

## 可安装的第三方库

`requirements.txt` 和 `requirements-optional.txt` 仅声明依赖，不打包其源码、二进制或字体。安装时请保留各库自身的许可证及发行声明。

| 项目 | 用途 | 上游 |
|---|---|---|
| NumPy / SciPy | 科学计算 | https://numpy.org/ / https://scipy.org/ |
| Matplotlib | 单幅科研图 | https://matplotlib.org/ |
| openpyxl | Excel结果文件 | https://openpyxl.readthedocs.io/ |
| Pillow | 图像检查 | https://python-pillow.org/ |
| pypdf / pdfplumber | PDF文字与版面检查 | https://github.com/py-pdf/pypdf / https://github.com/jsvine/pdfplumber |
| PyYAML | 技能元数据校验 | https://pyyaml.org/ |
| acadplot（可选） | 科研绘图风格 | https://github.com/sudip-bhujel/acadplot |
| boviz（可选） | 科学可视化辅助 | https://github.com/bo-qian/boviz |

`acadplot` 和 `boviz` 是Python库，不是本仓库内嵌的独立Codex技能。核心绘图工具使用Matplotlib，缺少可选库仍能运行。

## 外部服务与环境

文献工具通过公开的 Crossref REST API 和 DOI.org 内容协商核验元数据，不要求私有API密钥。证据保存在用户项目中，网络响应作为数据处理，不作为指令执行。

TeX Live / MiKTeX、XeLaTeX、BibTeX、Poppler和TeXstudio由使用者按本机平台安装。本仓库不重新发布这些软件。SimHei和其他系统字体不随包提供；跨平台可使用独立安装的Noto Sans CJK等适用字体。

技能发现与安装约定核验来源：OpenAI官方文档 https://developers.openai.com/codex/skills/ （核验日期2026-11-06）。
