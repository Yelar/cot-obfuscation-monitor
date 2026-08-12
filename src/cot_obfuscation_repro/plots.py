from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from .io import ROOT, read_json

MAIN_ORDER = ("behavioral", "monitor_aware", "a_only", "b_only", "ab")
RATIO_ORDER = ("a_only", "b_only", "ab", "monitor_aware")
GRID_COLOR = "#d9e0e6"
TEXT_COLOR = "#18232d"
PANEL_COLORS = ("#0072b2", "#e69f00", "#d55e00")
RATIO_STYLES = {
    "a_only": {"color": "#0072b2", "marker": "o", "linestyle": "-"},
    "b_only": {"color": "#e69f00", "marker": "s", "linestyle": "--"},
    "ab": {"color": "#009e73", "marker": "D", "linestyle": "-."},
    "monitor_aware": {"color": "#cc79a7", "marker": "^", "linestyle": (0, (1, 1.4))},
}
ARTIFACT_DATE = datetime(2026, 8, 12, tzinfo=timezone.utc)
ARTIFACT_DATE_ISO = "2026-08-12T00:00:00+00:00"


def _pdf_metadata(title: str) -> dict[str, Any]:
    return {
        "Title": title,
        "Creator": "cot_obfuscation_repro.plots",
        "Producer": "Matplotlib",
        "CreationDate": ARTIFACT_DATE,
        "ModDate": ARTIFACT_DATE,
    }


def _svg_metadata(title: str) -> dict[str, Any]:
    return {
        "Title": title,
        "Date": ARTIFACT_DATE_ISO,
        "Creator": "cot_obfuscation_repro.plots",
    }


def _mpl() -> Any:
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".runtime" / "matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42
    matplotlib.rcParams["svg.hashsalt"] = "cot-obfuscation-monitor-v1"
    import matplotlib.pyplot as plt
    return plt


def plot_main(source: Path, output: Path) -> None:
    plt = _mpl()
    data = read_json(source)["conditions"]
    short_labels = {
        "behavioral": "Behavioral\ncontrol",
        "monitor_aware": "Explicit\nmonitor",
        "a_only": "A-only",
        "b_only": "B-only",
        "ab": "A+B",
    }
    labels = [f"{short_labels[key]}\n(e{data[key]['selected_epoch']})" for key in MAIN_ORDER]
    jitter = (-0.14, -0.07, 0.0, 0.07, 0.14)
    specifications = (
        {
            "title": "Concealment rate",
            "subtitle": "C / (C + D)\n500 valid defect-present responses per seed",
            "metric": "concealment_rate_positive_valid",
            "complement": False,
            "ylim": (0, 100),
            "yticks": range(0, 101, 20),
        },
        {
            "title": "Conditional non-detection",
            "subtitle": "(C − K) / C\nConcealments (C); varies by condition and seed",
            "metric": "successful_detection_rate_all_concealments",
            "complement": True,
            "ylim": (0, 35),
            "yticks": range(0, 36, 5),
        },
        {
            "title": "Absolute uncaught deception",
            "subtitle": "(C − K) / N\nAll 1,000 scenarios per seed",
            "metric": "conservative_uncaught_rate_all_scenarios",
            "complement": False,
            "ylim": (0, 11),
            "yticks": range(0, 11, 2),
        },
    )
    fig, axes = plt.subplots(1, 3, figsize=(10.7, 4.4))
    for panel, (ax, specification, color) in enumerate(zip(axes, specifications, PANEL_COLORS)):
        for index, key in enumerate(MAIN_ORDER):
            metric = data[key]["metrics"][specification["metric"]]
            values = list(metric["replicate_values"])
            mean = float(metric["seed_rate_mean"])
            ci_low, ci_high = (float(value) for value in metric["mean_t_95"])
            if specification["complement"]:
                values = [1.0 - value for value in values]
                mean = 1.0 - mean
                ci_low, ci_high = 1.0 - ci_high, 1.0 - ci_low
            values_pct = [100.0 * value for value in values]
            mean_pct = 100.0 * mean
            ci_low_pct, ci_high_pct = 100.0 * ci_low, 100.0 * ci_high
            ax.scatter(
                [index + offset for offset in jitter],
                values_pct,
                s=24,
                color=color,
                alpha=0.50,
                edgecolor="white",
                linewidth=0.55,
                zorder=3,
            )
            ax.errorbar(
                index,
                mean_pct,
                yerr=[[mean_pct - ci_low_pct], [ci_high_pct - mean_pct]],
                fmt="D",
                markersize=5.5,
                color=color,
                markerfacecolor=color,
                markeredgecolor="white",
                markeredgewidth=0.6,
                elinewidth=1.8,
                capsize=4,
                capthick=1.8,
                zorder=4,
            )
            label_offset = 3.0 if panel == 0 else 1.05 if panel == 1 else 0.38
            ax.text(
                index,
                min(ci_high_pct + label_offset, specification["ylim"][1] - 0.7),
                f"{mean_pct:.1f}%",
                ha="center",
                va="bottom",
                fontsize=8.4,
                fontweight="bold",
                color=TEXT_COLOR,
            )
        ax.set_ylim(*specification["ylim"])
        ax.set_yticks(list(specification["yticks"]))
        ax.set_ylabel("Rate (%)", color=TEXT_COLOR)
        ax.set_xticks(range(len(MAIN_ORDER)), labels)
        ax.tick_params(axis="x", labelsize=7.8, colors=TEXT_COLOR, pad=4)
        ax.tick_params(axis="y", labelsize=8.5, colors=TEXT_COLOR)
        ax.grid(axis="y", color=GRID_COLOR, linewidth=0.8, alpha=0.8)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_color("#8d99a3")
            spine.set_linewidth(0.8)
        ax.set_title(specification["title"], loc="left", fontsize=11.5, fontweight="bold", color=color, pad=31)
        ax.text(
            0,
            1.035,
            specification["subtitle"],
            transform=ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=7.6,
            color="#5f6b75",
            linespacing=1.25,
        )
    fig.subplots_adjust(left=0.065, right=0.99, top=0.84, bottom=0.16, wspace=0.30)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220, bbox_inches="tight", pad_inches=0.03)
    fig.savefig(
        output.with_suffix(".pdf"),
        bbox_inches="tight",
        pad_inches=0.03,
        metadata=_pdf_metadata("Deception frequency and conditional monitorability across training conditions"),
    )
    plt.close(fig)


def _save_ratio_figure(fig: Any, output_base: Path, title: str) -> None:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_base.with_suffix(".pdf"),
        bbox_inches="tight",
        pad_inches=0.03,
        metadata=_pdf_metadata(title),
    )
    svg_output = output_base.with_suffix(".svg")
    fig.savefig(
        svg_output,
        bbox_inches="tight",
        pad_inches=0.03,
        metadata=_svg_metadata(title),
    )
    svg_text = svg_output.read_text(encoding="utf-8")
    svg_output.write_text("\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n", encoding="utf-8")
    fig.savefig(output_base.with_suffix(".png"), dpi=220, bbox_inches="tight", pad_inches=0.03)


def _plot_ratio_metric(families: dict[str, Any], specification: dict[str, Any], output_base: Path) -> None:
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(8.8, 4.05))
    shared_endpoint: tuple[float, tuple[float, float]] | None = None
    for family_key in RATIO_ORDER:
        if family_key not in families:
            continue
        points = families[family_key]["points"]
        xs = sorted(int(value) for value in points)
        metric = specification["metric"]
        ys = [100 * points[str(x)]["metrics"][metric]["seed_rate_mean"] for x in xs]
        intervals = [points[str(x)]["metrics"][metric]["mean_t_95"] for x in xs]
        lows = [100 * interval[0] for interval in intervals]
        highs = [100 * interval[1] for interval in intervals]
        style = RATIO_STYLES[family_key]
        ax.plot(
            xs,
            ys,
            color=style["color"],
            linestyle=style["linestyle"],
            linewidth=2.4,
            marker=style["marker"],
            markevery=range(len(xs) - 1),
            markersize=7.5,
            markerfacecolor="white",
            markeredgewidth=1.8,
            label=families[family_key]["label"],
            zorder=3,
        )
        ax.errorbar(
            xs[:-1],
            ys[:-1],
            yerr=(
                [mean - low for mean, low in zip(ys[:-1], lows[:-1])],
                [high - mean for mean, high in zip(ys[:-1], highs[:-1])],
            ),
            fmt="none",
            color=style["color"],
            elinewidth=1.8,
            capsize=5,
            capthick=1.8,
            zorder=2,
        )
        if shared_endpoint is None:
            shared_endpoint = (ys[-1], (lows[-1], highs[-1]))

    if shared_endpoint is None:
        raise ValueError("ratio plot requires at least one condition")
    endpoint_mean, (endpoint_low, endpoint_high) = shared_endpoint
    ax.errorbar(
        [100],
        [endpoint_mean],
        yerr=[[endpoint_mean - endpoint_low], [endpoint_high - endpoint_mean]],
        fmt="X",
        color="#5f6b75",
        markerfacecolor="white",
        markeredgewidth=1.8,
        markersize=8.5,
        elinewidth=2.0,
        capsize=5.5,
        capthick=2.0,
        zorder=5,
    )
    ax.annotate(
        "Shared endpoint",
        xy=(100, endpoint_mean),
        xytext=(-8, specification["endpoint_label_offset"]),
        textcoords="offset points",
        ha="right",
        va="bottom",
        fontsize=10.5,
        color="#5f6b75",
    )
    ax.set_xlim(-3, 103)
    ax.set_ylim(*specification["ylim"])
    ax.set_yticks(specification["yticks"])
    ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("Behavioral-document share in Stage 2 training data (%)", fontsize=12.5, labelpad=9)
    ax.set_ylabel(specification["ylabel"], fontsize=12.5, labelpad=8)
    ax.tick_params(axis="both", labelsize=11.5, colors=TEXT_COLOR)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.9)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#8d99a3")
        spine.set_linewidth(0.9)
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(0.0, 1.01),
        ncol=4,
        frameon=False,
        fontsize=11.0,
        handlelength=2.6,
        columnspacing=1.3,
    )
    fig.subplots_adjust(left=0.105, right=0.985, top=0.86, bottom=0.20)
    _save_ratio_figure(fig, output_base, specification["title"].replace("\n", " "))
    plt.close(fig)


def plot_ratio(source: Path, output_dir: Path) -> None:
    families = read_json(source)["families"]
    specifications = (
        {
            "metric": "concealment_rate_positive_valid",
            "title": "Concealment as a function of the\nbehavioral-document share",
            "ylabel": "Concealment rate (%)",
            "ylim": (20, 100),
            "yticks": [20, 40, 60, 80, 100],
            "endpoint_label_offset": 6,
            "filename": "figure_2_concealment_ratio",
        },
        {
            "metric": "conservative_uncaught_rate_all_scenarios",
            "title": "Absolute uncaught deception as a function of the\nbehavioral-document share",
            "ylabel": "Absolute uncaught deception (%)",
            "ylim": (0, 10),
            "yticks": [0, 2, 4, 6, 8, 10],
            "endpoint_label_offset": 6,
            "filename": "figure_3_uncaught_ratio",
        },
    )
    for specification in specifications:
        _plot_ratio_metric(families, specification, output_dir / specification["filename"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate plots from frozen or newly assembled summaries")
    parser.add_argument(
        "--main-results",
        type=Path,
        default=ROOT / "data" / "results" / "five_condition_corrected.json",
    )
    parser.add_argument(
        "--ratio-results",
        type=Path,
        default=ROOT / "data" / "results" / "behavioral_ratio_corrected.json",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "plots" / "generated")
    parser.add_argument("--main-only", action="store_true", help="Generate only Figure 1")
    args = parser.parse_args()
    plot_main(args.main_results, args.output_dir / "figure_1_three_metrics.png")
    if not args.main_only:
        plot_ratio(args.ratio_results, args.output_dir)
    print(f"wrote plots to {args.output_dir}")


if __name__ == "__main__":
    main()
