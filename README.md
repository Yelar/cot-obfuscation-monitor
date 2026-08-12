# Indirect monitor-facts fine-tuning in GPT-OSS-120B

This repository contains the experiment code, frozen data, configurations,
sanitized result summaries, and figure-generation tools for the compositional
monitor-facts study. It tests two synthetic-document families, reasoning is
retained (A) and deliberate deception has consequences (B), that were intended
to be combined into monitor awareness. The evaluation did not directly verify
that GPT-OSS-120B learned and combined those facts.

A+B had lower concealment and lower absolute uncaught deception, but the
results do not show improved detectability of deceptive reasoning conditional
on concealment. The explicit-monitor result is a directional partial
replication: absolute uncaught deception increased from 6.88% to 8.74%, much
less than previously reported.

## Fast paths

| Goal | Command | External calls |
|---|---|---|
| Verify data, tests, metrics, and figures | `bash scripts/reproduce.sh` | Package install only on first setup |
| Rebuild pinned public training views | `bash scripts/prepare_data.sh` | Hugging Face |
| Also build all ratio mixtures | `bash scripts/prepare_data.sh --ratios` | Hugging Face |
| Regenerate figures from frozen summaries | `bash scripts/build_figures.sh` | None after setup |
| Run the complete main experiment | `bash scripts/run_main_experiment.sh --confirm-paid-run --run-dir artifacts/runs/main-reproduction` | Paid Tinker and OpenRouter calls |
| Run the ratio ablation | `bash scripts/run_ratio_ablation.sh --confirm-paid-run --main-run artifacts/runs/main-reproduction --run-dir artifacts/runs/ratio-reproduction` | Paid Tinker and OpenRouter calls |
| Run both paid workflows | `bash scripts/run_all.sh --confirm-paid-run --run-root artifacts/runs/full-reproduction` | Paid Tinker and OpenRouter calls |

The paid commands are intentionally gated by `--confirm-paid-run`. The main
workflow starts 6 GPT-OSS-120B training runs, 25 selector evaluations, and 25
full evaluations. The ratio workflow adds 12 training runs, 60 selector
evaluations, and 36 full evaluations. Check current provider prices, quotas,
and model availability before starting.

## Environment setup

Python 3.11 or 3.12 and [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
are supported. Install `uv` once if needed, then create the locked environment
and a private `.env` template:

```bash
bash scripts/setup.sh full
```

Edit `.env` locally:

```dotenv
TINKER_API_KEY=           # training and sampling
OPENROUTER_API_KEY=       # monitoring and optional corpus generation
HF_TOKEN=                 # optional for public dataset downloads
WANDB_API_KEY=            # optional; launchers leave W&B disabled
MONITOR_WORKERS=12        # raise only if your OpenRouter quota permits
```

The setup script creates `.env` with mode `0600` and never overwrites an
existing file. `.env`, logs, checkpoint manifests, raw outputs, model
reasoning, and Tinker URIs remain under ignored `artifacts/` or `outputs/`
directories. Never commit them.

Use a smaller environment when paid dependencies are unnecessary:

```bash
bash scripts/setup.sh offline
bash scripts/setup.sh data
```

## What the automated workflows do

The main one-command workflow:

1. downloads and hash-verifies the pinned public training datasets;
2. trains the shared four-epoch Stage-1 checkpoint;
3. trains all five five-epoch Stage-2 conditions from the same Stage-1 state;
4. evaluates every Stage-2 epoch on the frozen 100-scenario selector;
5. selects one checkpoint per condition using the released rule;
6. evaluates each selected checkpoint on 1,000 scenarios with seeds 101, 202,
   303, 404, and 505;
7. aggregates conservative metrics with 95% Student-t intervals; and
8. generates Figure 1 from the newly produced main-condition summary.

Checkpoint locations are written incrementally to mode-`0600` JSON manifests.
Completed training and evaluation outputs are validated and reused. If a
training manifest is partial, the workflow stops and preserves it instead of
silently launching a duplicate paid run.

The ratio wrapper uses the completed main run as its Stage-1 parent, reuses the
three-seed 0% and 100% endpoints automatically, and then generates all three
figures from the completed main and ratio summaries.

## Repository map

| Path | Purpose |
|---|---|
| `config/` | Frozen protocols, dataset revisions, hashes, and result-manifest examples |
| `data/training/compositional/` | Final A-only, B-only, A+B, and component corpora |
| `data/scenarios/` | Frozen 100-scenario selector and balanced 1,000-scenario evaluation set |
| `data/results/` | Sanitized count-preserving summaries used for offline figures |
| `src/cot_obfuscation_repro/` | Preparation, evaluation, metrics, selection, aggregation, audit, and plotting tools |
| `scripts/` | One-command workflows, training launchers, and optional corpus generation |
| `tests/` | Credential-free protocol, integrity, and workflow tests |

Generated figures are written as PNG, PDF, and SVG under `plots/generated/`
or the selected run directory. Runtime data belong only under ignored
`artifacts/` or `outputs/` directories.

## Protocol at a glance

- Stage 1 uses 1,272 documents, batch size 32, four epochs, and seed 0.
- Each Stage-2 branch uses 1,000 documents, batch size 8, five epochs, and seed 0.
- Every Stage-2 branch starts from the same Stage-1 epoch-4 state with a fresh
  Adam optimizer. Adapters are not stacked.
- Epoch selection maximizes conservative absolute uncaught count on the frozen
  100 defect-present scenarios, with earlier-epoch tie-breaking.
- Final evaluation uses 1,000 scenarios and seeds 101, 202, 303, 404, and 505.
- The ratio ablation uses seeds 101, 202, and 303.
- Monitor-ineligible short-reasoning concealments and monitor errors count as
  uncaught.

See [REPRODUCING.md](REPRODUCING.md) for manual commands and recovery details,
[DATA.md](DATA.md) for provenance and hashes, [METHODOLOGY.md](METHODOLOGY.md)
for metric definitions, [RESULTS.md](RESULTS.md) for frozen values, and
[SECURITY.md](SECURITY.md) before publishing changes.

No reuse license has been granted yet; see [LICENSE.md](LICENSE.md).
