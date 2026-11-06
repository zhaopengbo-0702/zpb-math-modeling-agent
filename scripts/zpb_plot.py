"""Publication-sized, standalone Chinese scientific figures.

The helpers set a visual baseline; the caller remains responsible for choosing
an honest representation of the computed data and inspecting the exports.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.colors import LogNorm
from matplotlib.ft2font import FT2Font


BLUE = "#287F95"
VERMILLION = "#CC6945"
GREEN = "#429E79"
INK = "#202020"
GRID = "#E4E8E9"
CJK_CANDIDATES = (
    "SimHei", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",
    "WenQuanYi Micro Hei", "PingFang SC", "Heiti SC", "Arial Unicode MS",
)


def _supports_chinese(path):
    glyphs = FT2Font(str(path)).get_charmap()
    return all(ord(char) in glyphs for char in "时间位置浓度误差")


def find_chinese_font(font=None):
    """Resolve a named family or local font file without silent glyph fallback."""
    if font is not None and Path(font).is_file():
        font_manager.fontManager.addfont(str(Path(font).resolve()))
        path = str(Path(font).resolve())
        family = font_manager.FontProperties(fname=path).get_name()
        if not _supports_chinese(path):
            raise ValueError(f"Font does not contain the required Chinese glyphs: {path}")
        return family, path
    candidates = (font,) if font else CJK_CANDIDATES
    for family in candidates:
        try:
            path = font_manager.findfont(
                font_manager.FontProperties(family=family), fallback_to_default=False,
            )
        except ValueError:
            continue
        if _supports_chinese(path):
            return family, path
    raise RuntimeError(
        "No Chinese font is available. Install SimHei locally or an open font "
        "such as Noto Sans CJK SC, or pass --font /path/to/font.otf. "
        "The skill does not redistribute system fonts."
    )


def configure(font=None):
    """Set the SimHei-first Chinese baseline and return the resolved font path."""
    family, path = find_chinese_font(font)
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [family],
        "axes.unicode_minus": False,
        "font.size": 10,
        "axes.labelsize": 11,
        "axes.titlesize": 11,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "axes.labelcolor": INK,
        "axes.edgecolor": INK,
        "text.color": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.linewidth": 0.85,
        "axes.spines.top": True,
        "axes.spines.right": True,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "legend.frameon": False,
        "lines.linewidth": 1.6,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.transparent": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "mathtext.fontset": "stix",
        "axes.labelpad": 8,
    })
    return path


def single_axes(width=6.0, height=4.3, projection=None):
    """Create exactly one main axes; a colorbar may be attached separately."""
    fig = plt.figure(figsize=(width, height), layout="constrained")
    ax = fig.add_subplot(111, projection=projection)
    if projection is None:
        ax.tick_params(top=True, right=True, length=3.5)
        ax.set_axisbelow(True)
    return fig, ax


def positive_log_norm(values):
    """Choose logarithmic normalization only for finite, strictly positive data."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if not finite.size or np.any(finite <= 0):
        raise ValueError("Logarithmic color scales require strictly positive data.")
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        raise ValueError("A constant field does not need a logarithmic color scale.")
    return LogNorm(vmin=low, vmax=high)


def save_figure(fig, output, name, dpi=600):
    """Export one standalone figure as PDF, SVG and high-resolution PNG."""
    if dpi < 300:
        raise ValueError("Publication figure export must use at least 300 dpi.")
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("Use a filename stem, not a path, for the figure name.")
    primary_axes = [
        ax for ax in fig.axes
        if ax.get_label() != "<colorbar>" and not hasattr(ax, "_colorbar")
    ]
    if len(primary_axes) != 1:
        raise ValueError("Each export must contain exactly one main axes, not a panel collage.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()
    paths = []
    for extension in ("pdf", "svg", "png"):
        path = output / f"{name}.{extension}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.12)
        paths.append(path)
    plt.close(fig)
    return paths
