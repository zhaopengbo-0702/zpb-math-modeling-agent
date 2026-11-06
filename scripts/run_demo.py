"""An executed diffusion example with an analytic reference, not a paper generator."""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp, trapezoid
from scipy.sparse import diags


def solve_diffusion(intervals=80, diffusivity=0.08, threshold=0.2, samples=201):
    """Solve u_t=D*u_xx on (0,1), with u=0 at both ends and u(x,0)=sin(pi*x)."""
    if intervals < 4 or intervals % 2:
        raise ValueError("Use an even number of intervals, at least four.")
    if diffusivity <= 0 or not 0 < threshold < 1 or samples < 2:
        raise ValueError("Require D>0, 0<threshold<1 and at least two samples.")
    x = np.linspace(0.0, 1.0, intervals + 1)
    spacing = 1.0 / intervals
    operator = diffusivity / spacing**2 * diags(
        [np.ones(intervals - 2), -2 * np.ones(intervals - 1), np.ones(intervals - 2)],
        [-1, 0, 1], format="csc",
    )
    exact_stop = -np.log(threshold) / (np.pi**2 * diffusivity)

    def stop_event(t, state):
        return np.max(state) - threshold

    stop_event.terminal = True
    stop_event.direction = -1
    solution = solve_ivp(
        lambda t, state: operator @ state,
        (0.0, 1.4 * exact_stop), np.sin(np.pi * x[1:-1]),
        method="BDF", jac=operator, rtol=2e-10, atol=2e-12,
        max_step=exact_stop / 35, dense_output=True, events=stop_event,
    )
    if not solution.success or not len(solution.t_events[0]):
        raise RuntimeError(f"Diffusion integration did not reach the threshold: {solution.message}")
    numeric_stop = float(solution.t_events[0][0])
    times = np.linspace(0.0, numeric_stop, samples)
    field = np.zeros((samples, intervals + 1))
    field[:, 1:-1] = solution.sol(times).T
    analytic = np.exp(-np.pi**2 * diffusivity * times[:, None]) * np.sin(np.pi * x[None, :])
    errors = np.sqrt(trapezoid((field - analytic)**2, x=x, axis=1))
    return {
        "intervals": intervals, "diffusivity": diffusivity, "threshold": threshold,
        "x": x, "times": times, "field": field, "analytic": analytic,
        "l2_errors": errors, "numeric_stop": numeric_stop, "exact_stop": float(exact_stop),
        "stop_absolute_error": abs(numeric_stop - exact_stop),
        "max_l2_error": float(errors.max()), "function_evaluations": int(solution.nfev),
    }


def compute_demo(output, intervals=80, diffusivity=0.08, threshold=0.2, xlsx=True, overwrite=False):
    output = Path(output)
    if (output / "summary.json").exists() and not overwrite:
        raise FileExistsError(f"Demo output already exists: {output}; choose another directory or --overwrite.")
    output.mkdir(parents=True, exist_ok=True)
    grids = (20, 40, 80)
    runs = {n: solve_diffusion(n, diffusivity, threshold) for n in set(grids) | {intervals}}
    result = runs[intervals]
    convergence = [{
        "intervals": n, "spacing_m": 1 / n,
        "numeric_stop_s": runs[n]["numeric_stop"],
        "stop_absolute_error_s": runs[n]["stop_absolute_error"],
        "max_l2_error": runs[n]["max_l2_error"],
    } for n in grids]
    orders = [float(np.log(convergence[i]["stop_absolute_error_s"] /
                           convergence[i + 1]["stop_absolute_error_s"]) / np.log(2))
              for i in range(len(grids) - 1)]
    summary = {
        "case": "one-dimensional diffusion with known analytic solution",
        "equation": "u_t = D u_xx, x in (0,1), u(0,t)=u(1,t)=0, u(x,0)=sin(pi*x)",
        "analytic_solution": "u(x,t)=sin(pi*x)*exp(-pi^2*D*t)",
        "spatial_method": "second-order centered finite differences on a uniform grid",
        "time_method": "SciPy solve_ivp variable-order BDF/NDF",
        "event": "max_x u(x,t) crosses threshold downwards",
        "diffusivity_m2_per_s": diffusivity,
        "threshold": threshold,
        "intervals": intervals,
        "exact_stop_s": result["exact_stop"],
        "numeric_stop_s": result["numeric_stop"],
        "stop_absolute_error_s": result["stop_absolute_error"],
        "max_l2_error": result["max_l2_error"],
        "minimum_state": float(result["field"].min()),
        "maximum_state": float(result["field"].max()),
        "observed_stop_error_orders": orders,
        "grid_convergence": convergence,
        "xlsx_exported": False,
    }
    with (output / "field.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", *result["x"]])
        writer.writerows([[time, *row] for time, row in zip(result["times"], result["field"])])
    np.savez_compressed(output / "field.npz", x=result["x"], time_s=result["times"],
                        numeric=result["field"], analytic=result["analytic"])
    if xlsx:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill
        except ImportError:
            print("openpyxl is unavailable; CSV and NPZ have been written. Install openpyxl to export XLSX.")
        else:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "浓度场"
            sheet.append(["时间/s；首行位置/m", *map(float, result["x"])])
            for time, row in zip(result["times"], result["field"]):
                sheet.append([float(time), *map(float, row)])
            sheet.freeze_panes = "B2"
            for cell in sheet[1]:
                cell.font = Font(name="Arial", bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="287F95")
                cell.alignment = Alignment(horizontal="center")
            for row in sheet.iter_rows(min_row=2):
                for cell in row[1:]:
                    cell.number_format = "0.0000"
            sheet.column_dimensions["A"].width = 27
            workbook.save(output / "result.xlsx")
            summary["xlsx_exported"] = True
    facts = {
        "schema": "zpb.numericfacts.v1",
        "facts": [
            {"id": "demo.numeric_stop", "quantity": "数值全域达标时间", "value": summary["numeric_stop_s"],
             "unit": "s", "source": {"artifact": "summary.json", "locator": "/numeric_stop_s"}, "status": "computed"},
            {"id": "demo.exact_stop", "quantity": "解析全域达标时间", "value": summary["exact_stop_s"],
             "unit": "s", "source": {"artifact": "summary.json", "locator": "/exact_stop_s"}, "status": "analytic"},
            {"id": "demo.stop_error", "quantity": "停止时间绝对误差", "value": summary["stop_absolute_error_s"],
             "unit": "s", "source": {"artifact": "summary.json", "locator": "/stop_absolute_error_s"}, "status": "computed"},
        ],
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "numeric_facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result, summary


def draw_figure(result, summary, output, figure="field", font=None):
    from zpb_plot import BLUE, GREEN, INK, VERMILLION, configure, save_figure, single_axes
    import matplotlib.pyplot as plt

    resolved_font = configure(font)
    threshold = result["threshold"]
    if figure == "field":
        fig, ax = single_axes()
        image = ax.contourf(result["times"], result["x"], result["field"].T,
                            levels=np.linspace(0, 1, 81), cmap="cividis")
        boundary = ax.contour(result["times"], result["x"], result["field"].T,
                              levels=[threshold], colors=["white"], linewidths=1.6)
        ax.clabel(boundary, fmt={threshold: f"达标边界 {threshold:g}"}, fontsize=9)
        ax.plot(result["numeric_stop"], 0.5, "o", color=VERMILLION, markersize=5, clip_on=False)
        ax.set(xlabel="时间 / s", ylabel="位置 / m", xlim=(0, result["numeric_stop"]), ylim=(0, 1))
        colorbar = fig.colorbar(image, ax=ax, fraction=0.045, pad=0.035,
                                ticks=np.linspace(0, 1, 6), format="%.1f")
        colorbar.set_label("归一化浓度")
    elif figure == "surface":
        fig, ax = single_axes(width=6.5, height=4.8, projection="3d")
        time_grid, position_grid = np.meshgrid(result["times"], result["x"], indexing="ij")
        image = ax.plot_surface(time_grid, position_grid, result["field"], cmap="cividis",
                                linewidth=0, antialiased=True, shade=False, rasterized=True)
        ax.contour(time_grid, position_grid, result["field"], levels=[threshold],
                   colors=[VERMILLION], linewidths=1.4)
        ax.contour(time_grid, position_grid, result["field"], levels=[threshold],
                   zdir="z", offset=0, colors=[INK], linewidths=1.2)
        ax.set(xlabel="时间 / s", ylabel="位置 / m", zlabel="归一化浓度", zlim=(0, 1.03))
        ax.view_init(elev=27, azim=-132)
        ax.set_box_aspect((1.5, 1, 0.9))
        for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            axis.pane.fill = False
            axis.pane.set_edgecolor("#DCE2E5")
        colorbar = fig.colorbar(image, ax=ax, shrink=0.65, fraction=0.035, pad=0.06)
        colorbar.set_label("归一化浓度")
    elif figure == "convergence":
        from matplotlib.ticker import FuncFormatter, NullFormatter
        fig, ax = single_axes()
        grids = summary["grid_convergence"]
        spacing = np.array([item["spacing_m"] for item in grids])
        errors = np.array([item["stop_absolute_error_s"] for item in grids])
        reference = errors[0] * (spacing / spacing[0])**2
        ax.loglog(spacing, errors, "o-", color=BLUE, markerfacecolor="white", markersize=6,
                  label="实际停止时间误差")
        ax.loglog(spacing, reference, "--", color=GREEN, label="二阶参考斜率")
        ax.set(xlabel="空间步长 / m", ylabel="停止时间绝对误差 / s")
        ax.set_xticks(spacing)
        ax.set_yticks(errors)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.3g}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.grid(which="major", color="#E4E8E9", linewidth=0.6)
        ax.legend(loc="upper left")
    else:
        raise ValueError(f"Unknown figure: {figure}")
    paths = save_figure(fig, Path(output) / "figures", figure, dpi=600)
    (Path(output) / "figure_metadata.json").write_text(json.dumps({
        "figure": figure, "font_path": resolved_font, "dpi": 600,
        "source": "field.npz + summary.json", "exports": [path.name for path in paths],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--figure", choices=("field", "surface", "convergence"), default="field")
    parser.add_argument("--compute-only", action="store_true", help="Run without Matplotlib or Chinese fonts.")
    parser.add_argument("--font", help="Chinese font family or a local TTF/OTF path.")
    parser.add_argument("--intervals", type=int, default=80)
    parser.add_argument("--diffusivity", type=float, default=0.08)
    parser.add_argument("--threshold", type=float, default=0.2)
    parser.add_argument("--skip-xlsx", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result, summary = compute_demo(
        args.output, args.intervals, args.diffusivity, args.threshold,
        xlsx=not args.skip_xlsx, overwrite=args.overwrite,
    )
    if not args.compute_only:
        for path in draw_figure(result, summary, args.output, args.figure, args.font):
            print(path)
    print(json.dumps({key: summary[key] for key in (
        "numeric_stop_s", "exact_stop_s", "stop_absolute_error_s", "observed_stop_error_orders",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    main()
