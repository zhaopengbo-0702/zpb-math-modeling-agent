# 环境、输入与文献核验

## 初始化与依赖

在新工作目录执行技能工具，不在输入目录里生成或覆盖论文。建议 Python 3.11 及以上；数值计算、绘图和工作簿分别依赖 NumPy/SciPy、Matplotlib、openpyxl。PDF 检查使用 pypdf/pdfplumber；渲染优先使用 Poppler 的 `pdftoppm`。LaTeX 需要 XeLaTeX 和模板指定的 BibTeX 或 Biber，允许使用已安装的 MiKTeX 或 TeX Live。

```console
python scripts/preflight.py --output project-environment.json
python scripts/init_project.py --output work/paper --input question.pdf --input data.xlsx --template supplied-template --title "当前题目名称"
```

`--input` 每次接收一个文件，可重复。输入副本位于 `inputs/`，其 SHA256 记录在 `notes/input-manifest.json`。工具拒绝非空输出目录和重名输入。用户模板完整复制到 `template/`，基线位于 `notes/template-baseline.json`；智能体选择实际主文件，并沿用模板的类文件、样式和页面参数。无模板时复制技能的原创中文备用模板。`project.json` 中的默认结构和样式可根据用户明确要求调整。

预检只报告缺失依赖，不安装软件。依赖可按仓库 requirements 文件安装；使用当前智能体环境提供的 Python 时，应先定位其实际可执行路径，不能写死用户名或某台机器的缓存路径。先确认已有 TeX 工具；安装编辑器或大型 TeX 发行版只在用户要求或明确需要时执行。不能用安装 TeXstudio 代替安装 TeX 编译器。

预检中的 `ready_for_pdf` 表示已找到程序和检查库，实际模板的宏包及TeX字体要由首次编译确认。备用模板采用ctex的平台默认字体选择，避免固定某台电脑的fontset。用户模板的字体设置原样保留，缺失时记录并安装适用依赖或与用户要求协调。

有用户模板时，`template/` 保留原件；智能体在工程根目录准备活动正文及其本地类、样式、参考文献和资源。第一次用 `build_paper.py --main example.tex` 明确入口，成功后脚本记录 `project.json.manuscript.main`，后续构建、审计和打包统一沿用。

构建器根据本轮辅助文件自动选择 BibTeX 或 Biber。可用 `--bibtex PATH`、`--biber PATH` 显式指定程序。手写 `thebibliography` 不需要外部后端；审计识别活动 `bibitem`，单一年份可执行近年检查，年份缺失或有歧义时人工核验。

## 中文字体

优先设置：

```python
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False
```

字体不存在时，选择实际可用的 Noto Sans CJK SC、思源黑体或微软雅黑并记录替代。不要下载和再分发没有相应许可的商业字体，也不要静默退回缺少汉字的默认英文字体。绘制中文样张检查负号、单位、上下标和导出的 PDF 字形。

## 近年文献的两层核验

默认选择当前年份及之前五年正式出版的文献。与当前问题直接相关的近年研究优先；必要的经典定理出处允许保留，但不能把重印年份当作新研究。用户要求全部使用近年文献时，选择确实涉及该方法的近期正规论文或专著，重写相关背景句，避免把经典方法的首创归给近期作者。

第一层核对真实身份：题名、作者、期刊或出版社、正式年份、卷期和页码或文章号、DOI。第二层核对引用论点：读摘要或相关全文，记录它支持当前哪一句话。仅有题名和元数据时，只能使用题名直接支持的窄范围描述。不能把 Crossref 元数据通过等同于全文支持，也不能将他文的实验结果、精度或加速比写成本文结果。

`verify_references.py` 完成第一层，保存原始公开元数据缓存和带有待审论点的证据台账；第二层由智能体完成。请求 JSON 示例：

```json
[
  {
    "key": "method2024",
    "doi": "10.xxxx/replace-with-an-actual-verified-doi",
    "claim": "准备引用的一句具体论述",
    "location": "sections/method.tex 的相应段落"
  }
]
```

示例 DOI 是输入结构说明，必须换成检索到的真实 DOI 后运行。

```console
python scripts/verify_references.py reference-requests.json --output notes/reference-evidence.json --bibtex references.bib --cache notes/reference-cache --resolve-doi
```

默认近期下限为核验年份减五。`--as-of YYYY-MM-DD` 固定日期，`--min-year YYYY` 显式调整；脚本优先使用已经出版的印刷卷期年份，同时记录在线年份。未来出版日、重复 DOI、缺失作者、期刊卷号和页码等会报错。`--resolve-doi` 增加 DOI.org 内容协商核对，正式读文还应访问出版社或开放仓储。网络失败会留下错误记录，不会生成虚构条目；有任何失败时不覆盖既有 BibTeX。`--offline` 只使用已有缓存，不产生“在线验证通过”的表述。

## 正文引用位置

删除独立文献综述是本技能的默认论文结构，不代表删除学术引用。将编号放在被支持的背景句、方法引入句或比较讨论句后。一个来源重复引用时沿用同一 key；首次出现顺序由 BibTeX 顺序样式自动生成。本文推导、题设参数和本文实际计算结果分别清楚归属，不要在每个自推公式末尾机械附引文。

提交前核查：所有活动引用均有条目、所有条目实际引用、无重复 DOI、论点与出处相符、没有把在线年份和卷期年份混用，目录与 PDF 引文编号均为最后一轮构建结果。
