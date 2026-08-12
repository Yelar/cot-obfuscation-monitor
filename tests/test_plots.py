from __future__ import annotations

from cot_obfuscation_repro.io import ROOT
from cot_obfuscation_repro.plots import plot_main, plot_ratio


def test_frozen_summaries_render(tmp_path) -> None:
    main = tmp_path / "main.png"
    plot_main(ROOT / "data/results/five_condition_corrected.json", main)
    plot_ratio(ROOT / "data/results/behavioral_ratio_corrected.json", tmp_path)
    ratio_outputs = tuple(
        tmp_path / f"figure_{number}_{metric}_ratio.{suffix}"
        for number, metric in ((2, "concealment"), (3, "uncaught"))
        for suffix in ("pdf", "svg", "png")
    )
    for path in (main, main.with_suffix(".pdf"), *ratio_outputs):
        assert path.is_file()
        assert path.stat().st_size > 1_000
