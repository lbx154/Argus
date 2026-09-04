#!/usr/bin/env python3
"""Render Scenario 5 through Argus's Paper Chart Styling helper."""

from __future__ import annotations
import os

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.dont_write_bytecode = True


STYLE_HELPER = Path(os.environ.get("PAPER_CHART_STYLE", Path(__file__).resolve().parents[3] / "argus_skill/verticals/research/skills/engineer/figure_spec_scripts/paper_chart_style.py"))
BENCHMARKS = ("ImageNet", "CIFAR-100", "COCO")
VARIANTS = ("Full Model", "w/o Attention", "w/o Residual", "w/o Normalization")
HATCHES = ("", "///", "\\\\", "xx")
BAR_VALUE_FONT_PT = 8.0
BACKGROUND = "#fbfaf7"
STROKE = "#1f2933"
PASTEL_COLORS = ("#ffe2d1", "#fff2bd", "#dcecff", "#e2f7df")


def _load_style_helper() -> Any:
    spec = importlib.util.spec_from_file_location("argus_paper_chart_style", STYLE_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Paper Chart Styling helper: {STYLE_HELPER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def generate_simulated_results(seed: int = 2026) -> tuple[np.ndarray, np.ndarray]:
    """Reproduce baseline/scenario_5_ablation.py values exactly."""
    expected_accuracy = np.array(
        [
            [82.4, 88.6, 75.8],
            [79.1, 85.4, 72.2],
            [80.3, 86.1, 73.4],
            [77.8, 83.9, 70.5],
        ]
    )
    run_variation = np.array(
        [
            [0.35, 0.28, 0.52],
            [0.48, 0.41, 0.63],
            [0.42, 0.36, 0.58],
            [0.55, 0.47, 0.70],
        ]
    )
    rng = np.random.default_rng(seed)
    runs = rng.normal(expected_accuracy, run_variation, size=(5, 4, 3))
    return runs.mean(axis=0), runs.std(axis=0, ddof=1)


def render(output_dir: Path, basename: str = "scenario_5_ablation_results") -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    helper = _load_style_helper()

    # set_pub_style is the required Argus route.  Its helper explicitly applies
    # SciencePlots' science + no-latex styles before setting venue-aware sizes.
    helper.set_pub_style(venue="AAAI", column="single", palette="colorblind")
    colors = list(PASTEL_COLORS)
    style_applied = "science + no-latex via paper_chart_style.set_pub_style"
    plt.rcParams["svg.fonttype"] = "none"
    plt.rcParams["svg.hashsalt"] = "scenario-5-ablation-2026"
    figure_dimensions = helper.figure_size(column="single", venue="AAAI")
    means, stds = generate_simulated_results()

    fig, ax = plt.subplots(figsize=figure_dimensions)
    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.205, right=0.985, bottom=0.215, top=0.69)
    x = np.arange(len(BENCHMARKS))
    bar_width = 0.18
    offsets = (np.arange(len(VARIANTS)) - 1.5) * bar_width
    containers = []
    for index, (variant, hatch) in enumerate(zip(VARIANTS, HATCHES, strict=True)):
        container = ax.bar(
            x + offsets[index],
            means[index],
            width=bar_width,
            yerr=stds[index],
            label=variant,
            color=colors[index],
            edgecolor=STROKE,
            linewidth=1.0,
            hatch=hatch,
            error_kw={"ecolor": STROKE, "elinewidth": 0.8, "capsize": 2.5, "capthick": 0.8},
            zorder=3,
        )
        containers.append(container)

    for variant_index, container in enumerate(containers):
        for benchmark_index, bar in enumerate(container.patches):
            height = float(bar.get_height())
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height + float(stds[variant_index, benchmark_index]) + 0.45,
                f"{height:.1f}",
                ha="left",
                va="center",
                fontsize=BAR_VALUE_FONT_PT,
                color="#202020",
                rotation=90,
                rotation_mode="anchor",
                clip_on=True,
            )

    ax.set_ylabel("Top-1 Accuracy (%)\n(axis starts at 60%)", labelpad=3)
    ax.set_xticks(x, BENCHMARKS)
    ax.set_ylim(60, 98)  # headroom for the rotated 8pt value label above the tallest bar (88.8 + SD)
    ax.set_yticks(np.arange(60, 91, 10))
    ax.grid(axis="y", color="#B8B8B8", linewidth=0.6, alpha=0.45, zorder=0)
    ax.grid(axis="x", visible=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", which="both", top=False, right=False)
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.03),
        ncol=2,
        frameon=False,
        columnspacing=0.9,
        handletextpad=0.4,
        borderaxespad=0.0,
    )

    outputs = {
        suffix: output_dir / f"{basename}.{suffix}"
        for suffix in ("pdf", "svg", "png")
    }
    for suffix, path in outputs.items():
        save_kwargs: dict[str, Any] = {
            "format": suffix,
            "dpi": 300,
            "facecolor": BACKGROUND,
            "bbox_inches": "tight",
        }
        if suffix == "svg":
            save_kwargs["metadata"] = {
                "Creator": "scenario_5_ablation_studio.py",
                "Date": "2026-01-01T00:00:00",
            }
        fig.savefig(path, **save_kwargs)

    font_sizes = {
        "font": float(plt.rcParams["font.size"]),
        "axes_title": float(plt.rcParams["axes.titlesize"]),
        "axes_label": float(plt.rcParams["axes.labelsize"]),
        "x_tick": float(plt.rcParams["xtick.labelsize"]),
        "y_tick": float(plt.rcParams["ytick.labelsize"]),
        "legend": float(plt.rcParams["legend.fontsize"]),
        "bar_value": BAR_VALUE_FONT_PT,
    }
    below_minimum = {name: size for name, size in font_sizes.items() if size < 7.0}
    if below_minimum:
        raise RuntimeError(f"physical font size below 7pt: {below_minimum}")

    rendered_text_sizes = sorted(
        {
            float(text.get_fontsize())
            for text in fig.findobj(matplotlib.text.Text)
            if text.get_visible() and text.get_text()
        }
    )
    rendered_below_minimum = [size for size in rendered_text_sizes if size < 7.0]
    if rendered_below_minimum:
        raise RuntimeError(
            f"rendered text below 7pt: {rendered_below_minimum}"
        )
    plt.close(fig)

    result = {
        "style_helper": str(STYLE_HELPER),
        "style_applied": style_applied,
        "palette": colors[: len(VARIANTS)],
        "figure_size_inches": list(figure_dimensions),
        "font_sizes_pt": font_sizes,
        "minimum_font_pt": min(font_sizes.values()),
        "rendered_text_sizes_pt": rendered_text_sizes,
        "means": means.round(8).tolist(),
        "standard_deviations": stds.round(8).tolist(),
        "outputs": {suffix: str(path) for suffix, path in outputs.items()},
    }
    metadata_dir = output_dir
    if output_dir == Path(__file__).resolve().parent:
        metadata_dir = output_dir / "out"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / f"{basename}_style.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--basename", default="scenario_5_ablation_results")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = render(args.output_dir, args.basename)
    except Exception as exc:
        print(f"scenario_5_ablation_studio failed: {exc}")
        return 1
    print("Scenario 5 ablation chart rendered successfully.")
    print(f"Style: {result['style_applied']}")
    print(f"Figure size: {result['figure_size_inches']} in (AAAI single column)")
    print(f"Font sizes: {result['font_sizes_pt']} pt")
    print(f"Minimum font: {result['minimum_font_pt']:.1f} pt")
    print(f"Runs: 5; uncertainty: sample standard deviation; seed: 2026")
    print("Outputs: " + ", ".join(result["outputs"].values()))
    print("Style JSON: studio/out/scenario_5_ablation_results_style.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
