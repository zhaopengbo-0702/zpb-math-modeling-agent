# 多题独立工程与批量验收

本流程适用于一次处理多道题目或一组研究项目。智能体逐题完成分析、计算、绘图和写作，工具管理独立工程及后续批量构建。`batch_projects.py` 不调用模型 API；每个项目的正文和计算代码由当前智能体依据该题材料完成。

## 1. 准备清单

把原始题目、附件和可选模板放在输入目录，输出使用另一个目录。清单内的输入及模板路径相对清单文件解析。题目之间可以有同名附件，每个工程会保留自己的副本。

```json
{
  "jobs": [
    {
      "id": "paper-001",
      "title": "题目一的中文标题",
      "inputs": ["题目一/题目.pdf", "题目一/附件.xlsx"],
      "template": "模板"
    },
    {
      "id": "paper-002",
      "title": "题目二的中文标题",
      "inputs": ["题目二/题目.txt", "题目二/附件.csv"]
    }
  ]
}
```

`id` 使用不超过 64 字符的小写英文、数字及连字符，并在整份清单中唯一。输出目录为 `OUTPUT/paper-001/`、`OUTPUT/paper-002/`。路径逃逸、重复 ID、输入目录与输出目录重叠会在开始前报错。

```bash
python scripts/batch_projects.py --manifest cases/batch.json --output-root batch-output --stage initialize
```

成功后每题都有 `project.json`、输入哈希清单、独立结果目录及 `notes/batch-job.json`。不提供模板时使用内置中文骨架；提供模板时保留原始副本及哈希，由智能体按照模板建立正文入口。

## 2. 逐题完成科学工作

对每个工程依次执行主技能的完整流程：提取任务契约，选择适合该题的高阶数学主线，实际运行计算并完成数值验证，再生成独立中文科研图和正文。读题前不可套用其他题目的参数、结果或推导结论。

每题单独维护 `notes/task-contract.md`、`notes/method-ledger.json`、`numeric_facts.json`、引用证据和运行配置。相同算法可复用经过验证的实现，但参数、随机种子、训练数据、模型状态、数值结果、图件与段落必须与当前题目对应。摘要逐问的数值从该题结果源读取。

先用小规模计算估计耗时，再分配每题预算。优先完成题目必答结果、主要数值误差及关键敏感性；需要昂贵反演、降阶或随机传播时保留中间状态。方法记录使用 `derived`、`implemented`、`validated`、`extension`，正文用语与实际完成状态一致。预算变化不改变已经计算的数值，也不把尚未执行的方法写成已有结果。

## 3. 串行批量构建

确认每题正文、图件和引用已经完成，并在各自 `project.json` 中配置主文件及 `required_deliverables`，然后运行：

```bash
python scripts/batch_projects.py --manifest cases/batch.json --output-root batch-output --stage build --report reports/build.json
python scripts/batch_projects.py --manifest cases/batch.json --output-root batch-output --stage audit --report reports/audit.json
python scripts/batch_projects.py --manifest cases/batch.json --output-root batch-output --stage package --report reports/package.json
```

三个阶段分别调用技能的构建、审计和打包脚本，按清单顺序串行执行。每题日志保存在该工程的 `notes/batch/`，汇总报告记录阶段、状态、子进程 PID 和日志相对路径。单题失败后继续处理后续题；只要本次有失败，命令退出码为 `1`。报告会逐题更新，默认写到输出根目录 `batch-report.json`；自定义报告也必须位于输出根目录，并避开各题工程。

批量审计后逐题查看第一页摘要、改变过的正文页、全部图件及参考文献页。通过自动审计的报告不能代替数学推导核对和目视检查。失败项目修复后单独重跑：

```bash
python scripts/batch_projects.py --manifest cases/batch.json --output-root batch-output --stage build --job paper-002
python scripts/batch_projects.py --manifest cases/batch.json --output-root batch-output --stage audit --job paper-002
python scripts/batch_projects.py --manifest cases/batch.json --output-root batch-output --stage package --job paper-002
```

`--job` 可重复使用，执行顺序仍与清单一致。

## 4. 续跑与交付

对同一清单重复 `initialize` 时，已经存在且属于本技能的工程只有在标题、输入清单、原始输入哈希和模板哈希一致时才会续跑；保留用户已经写好的代码和正文。工程内的输入副本也会复核。原始题目或模板改变时使用新的 `id`，避免把两个题目的结果混在一起。非空目录缺少 `project.json`，或已有工程属于其他技能时，该题失败并在报告中说明。

每题独立交付最终 PDF、规定结果文件、核心提交包及复现包。公开仓库、投稿平台、Overleaf 共享或其他外部发布按用户指示执行，批量工具只在本地工作。
