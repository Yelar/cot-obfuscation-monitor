# Results

The table below is the frozen conservative recalculation. Point estimates pool direct counts across five 1,000-scenario replicates (seeds 101, 202, 303, 404, 505).

| Condition | Concealment | Conditional non-detection | Absolute uncaught deception |
|---|---:|---:|---:|
| Behavioral control | 89.92% | 15.30% | 6.88% |
| Explicit monitor | 66.24% | 26.39% | 8.74% |
| A-only | 46.72% | 21.92% | 5.12% |
| B-only | 62.32% | 24.65% | 7.68% |
| A+B | 40.16% | 18.13% | 3.64% |

The explicit-monitor continuation directionally increased absolute uncaught deception from 6.88% to 8.74%, recovering the direction but not the magnitude of the prior work's reported 1.7% to 25.7% increase. A+B had the lowest concealment and absolute uncaught-deception point estimates, but its remaining concealments were not easier for the monitor to detect than those of behavioral control.

The behavioral-ratio ablation is generally positive but not uniformly monotonic. A+B has the lowest point estimate at the non-100% ratios shown, while conditions converge at 100% behavioral documents. Full corrected values and three-seed intervals are in `data/results/behavioral_ratio_corrected.json`.

These results do not determine whether the prior explicit-monitor effect came from out-of-context reasoning, learned lexical or semantic associations, or another mechanism. In this evaluation of GPT-OSS-120B, A+B's lower absolute rate reflects less concealment instead of lower non-detection conditional on concealment.

The frozen JSON stores `successful_detection_rate_all_concealments`; conditional non-detection is its complement. Validate the calculations and regenerate all figures with:

```bash
bash scripts/reproduce.sh
```

- **Figure 1** reports concealment, conditional non-detection, and absolute uncaught deception as separate panels, with seed points and 95% Student-t intervals.
- **Figure 2** reports concealment across behavioral-document shares.
- **Figure 3** reports absolute uncaught deception across behavioral-document shares.

Figures 2 and 3 are exported as PDF, SVG, and PNG with redundant color, marker-shape, and line-style encodings. Use `bash scripts/build_figures.sh` for the frozen summaries or pass newly assembled result summaries with `--main-results` and `--ratio-results`.
