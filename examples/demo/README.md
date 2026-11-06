# 有解析解的扩散计算示例

此示例检验技能的数值计算、证据记录与图件工具。它不是所有建模题的通用模型，也不会把示例数值自动写入新题论文。

求解

```text
u_t = D u_xx, 0 < x < 1,
u(0,t) = u(1,t) = 0,
u(x,0) = sin(pi*x), D = 0.08 m^2/s.
```

解析解为 `sin(pi*x)*exp(-pi^2*D*t)`。以全域最大浓度首次降至 `0.2` 为事件，解析停止时间为 `-log(0.2)/(pi^2*D)`。程序使用二阶中心差分和 SciPy 变阶 BDF/NDF，比较 20、40、80 个空间区间；记录解析误差、停止时间误差和观测收敛阶。

在技能根目录运行：

```bash
python scripts/run_demo.py --output /path/to/demo-field --figure field
python scripts/run_demo.py --output /path/to/demo-surface --figure surface
python scripts/run_demo.py --output /path/to/demo-convergence --figure convergence
```

每个命令只生成一张独立图，各有 PDF、SVG 与 600 dpi PNG。需要显式选择字体时附加 `--font "Noto Sans CJK SC"`，也可传入本地字体文件路径。默认优先使用 SimHei，技能不分发字体。

无中文字体或只需验证计算时：

```bash
python scripts/run_demo.py --output /path/to/demo-numeric --compute-only
```

输出 `summary.json`、`numeric_facts.json`、全精度 `field.csv` 和 `field.npz`；安装了 openpyxl 时另导出 `result.xlsx`，单元格显示四位小数但保留原始浮点数。`numeric_facts.json` 记录每个数值的量纲、状态与结果文件位置，供论文中的数字逐项核对。

程序避免覆盖已有示例结果；明确需要重新计算时使用 `--overwrite`。不要把发布用示例输出混入其他题目的结果目录。
