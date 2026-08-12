# Reproducing the experiments

Use the lowest-cost workflow that answers your question: bundled offline verification, reconstruction of pinned public data, paid numerical reproduction, or optional generation of new synthetic-document candidates.

## 1. Environment and offline reproduction

Python 3.11 or 3.12 is supported. The historical training environment used Python 3.12, `tinker==0.23.4`, `tinker-cookbook==0.5.2`, and `datasets==5.0.0`.

```bash
# Install uv first if it is not already available:
# https://docs.astral.sh/uv/getting-started/installation/
bash scripts/reproduce.sh
```

`scripts/reproduce.sh` syncs the committed `uv.lock`, audits frozen public inputs, recomputes checks over the sanitized result summaries, runs the tests, and regenerates figures under `plots/generated/`. It makes no Tinker, OpenRouter, Codex, or Hugging Face calls. The one-time setup can access Python package indexes.

For any paid stage, install all optional dependencies and keep credentials in the ignored local `.env` file:

```bash
bash scripts/setup.sh full
# Fill TINKER_API_KEY and OPENROUTER_API_KEY in .env locally.
```

The setup script creates `.env` with mode `0600` if it is missing and never overwrites it. Empty values are safe. Do not add quotes containing placeholder text.

## Automated paid workflows

The complete main experiment is one command after `.env` is configured:

```bash
bash scripts/run_main_experiment.sh \
  --confirm-paid-run \
  --run-dir artifacts/runs/main-reproduction
```

Run the ratio ablation from that completed main run:

```bash
bash scripts/run_ratio_ablation.sh \
  --confirm-paid-run \
  --main-run artifacts/runs/main-reproduction \
  --run-dir artifacts/runs/ratio-reproduction
```

To run both sequentially:

```bash
bash scripts/run_all.sh \
  --confirm-paid-run \
  --run-root artifacts/runs/full-reproduction
```

Every private output is kept under the selected ignored run directory. The wrappers write checkpoint manifests after every save, reuse validated complete outputs, resume monitoring from raw samples and caches, enforce scenario hashes and seed sets, aggregate results, and generate figures. A partial training manifest is preserved and causes a clear stop so an interrupted paid run is not silently duplicated.

## 2. Condition names

Historical artifacts use two internal naming conventions. The launch scripts and frozen results map as follows:

| Public label | `train_stage2.sh` argument | Main-result key | Ratio-arm prefix |
|---|---|---|---|
| Behavioral control | `behavioral` | `behavioral` | shared 100% endpoint |
| Explicit monitor | `explicit_monitor` | `monitor_aware` | `monitor_aware` |
| A-only | `a_only` | `a_only` | `a_only` |
| B-only | `b_only` | `b_only` | `b_only` |
| A+B | `a_plus_b` | `ab` | `ab` |

## 3. Reconstruct pinned upstream training views

This stage needs network access to Hugging Face, but no paid model API. The wrapper installs the locked data dependencies if the environment is not present.

```bash
bash scripts/prepare_data.sh
```

The command downloads immutable revisions and produces:

- `artifacts/datasets/author/stage1_behavioral_1272/`
- `artifacts/datasets/author/stage2_behavioral_1000/`
- `artifacts/datasets/author/stage2_explicit_monitor_1000/`

The explicit-monitor view adds the original row index, then takes the first 1,000 rows after `Dataset.shuffle(seed=42)` over the pinned 4,000-row source. Its provenance-only `source_index` field is ignored by training but retained in the frozen JSONL hash.

Preparation is idempotent: complete existing views are hash-checked and reused, while missing views are reconstructed and verified before use.

## 4. Train the shared Stage 1 checkpoint

Use Bash with `pipefail` so a failed training process is not hidden by `tee`:

```bash
mkdir -p artifacts/runs/manual/stage1
set -o pipefail
bash scripts/train_stage1.sh artifacts/runs/manual/stage1/checkpoints.json \
  2>&1 | tee artifacts/runs/manual/stage1/training.log
```

Contract: GPT-OSS-120B, 1,272 rows, raw `<|doc|>` synthetic document fine-tuning, learning rate `1e-4`, batch size 32, LoRA rank 32, seed 0, four epochs. The mode-`0600` checkpoint manifest records the final epoch-4 **state** URI. It is the common parent for every Stage 2 run; a sampler URI cannot be used for continued training.

Because 1,272 is not divisible by batch size 32, the first three saved epoch labels occur around boundary-crossing batches. The final epoch-4 state still contains exactly four passes over the corpus and is the only Stage 1 checkpoint used downstream.

## 5. Train the five Stage 2 conditions

Run every branch in an independent client using the same Stage 1 state URI:

```bash
PARENT="$(.venv/bin/python -m cot_obfuscation_repro.workflow checkpoint-uri \
  artifacts/runs/manual/stage1/checkpoints.json --epoch 4 --kind state)"
mkdir -p artifacts/runs/manual/stage2
set -o pipefail
for CONDITION in behavioral explicit_monitor a_only b_only a_plus_b; do
  mkdir -p "artifacts/runs/manual/stage2/${CONDITION}"
  bash scripts/train_stage2.sh \
    "$CONDITION" "$PARENT" \
    "artifacts/runs/manual/stage2/${CONDITION}/checkpoints.json" \
    2>&1 | tee "artifacts/runs/manual/stage2/${CONDITION}/training.log"
done
```

Contract per branch: 1,000 rows, learning rate `1e-5`, batch size 8, rank 32, seed and shuffle seed 0, the same permutation each epoch, five epochs, fresh optimizer, no optimizer restoration, no adapter stacking, no replay, and no truncation.

## 6. Select one checkpoint per condition

Evaluate all five epoch sampler URIs from each Stage 2 branch on the frozen 100 defect-present scenarios. Use seed 101 for every selector call. For example:

```bash
bash scripts/evaluate_checkpoint.sh \
  'tinker://YOUR-RUN/sampler_weights/YOUR-EPOCH' \
  data/scenarios/frozen_defect_present_100.json \
  101 \
  artifacts/selector/a_only_epoch_1.json
```

After all five files for one condition exist:

```bash
.venv/bin/python -m cot_obfuscation_repro.select \
  artifacts/selector/a_only_epoch_*.json \
  --output artifacts/selector/a_only_selected.json
```

Repeat for every condition. Selection maximizes conservative uncaught count and breaks ties toward the earlier epoch. Do not use the 1,000-scenario results to reselect a checkpoint.

## 7. Evaluate selected checkpoints across five seeds

For each selected sampler URI, run seeds 101, 202, 303, 404, and 505 on the balanced full set:

```bash
for SEED in 101 202 303 404 505; do
  bash scripts/evaluate_checkpoint.sh \
    'tinker://YOUR-SELECTED/sampler_weights/YOUR-EPOCH' \
    data/scenarios/scenarios_mixed_1000.json \
    "$SEED" \
    "artifacts/full/a_only_seed_${SEED}.json"
done
```

Sampling uses temperature 0.6, 4,096 maximum tokens, three deterministic distractors, deterministic per-scenario sample seeds, and up to five resamples after an evasive or error first response. Each eligible concealment is freshly judged through OpenRouter by `openai/gpt-5-mini` at temperature zero with the released deception-v5 rubric.

The model name is a hosted alias, not a pinned model snapshot. Current provider behavior, availability, quotas, prices, and monitor judgments can differ from the historical run.

## 8. Behavioral-ratio ablation

If Section 3 already reconstructed the author views, build only the ratio mixtures:

```bash
bash scripts/prepare_data.sh --ratios
```

From a clean artifact directory, both operations can instead be combined once:

```bash
.venv/bin/python -m cot_obfuscation_repro.prepare --download-author --build-ratios
```

Train the 12 intermediate arms:

```bash
for FAMILY in a_only b_only ab monitor_aware; do
  for PERCENT in 25 50 75; do
    ARM="${FAMILY}_behavioral_${PERCENT}"
    bash scripts/train_ratio_arm.sh \
      "$ARM" "$PARENT" "artifacts/runs/manual/ratio/${ARM}/checkpoints.json"
  done
done
```

Screen all five epochs for each arm and evaluate each selected checkpoint with seeds 101, 202, and 303. The 0% and 100% endpoints reuse the corresponding three-seed main-condition and behavioral-control results. Preparation verifies every intermediate mixture against its frozen historical JSONL hash.

## 9. Metrics and plots

Recalculate one monitored payload:

```bash
.venv/bin/python -m cot_obfuscation_repro.metrics artifacts/full/a_only_seed_101.json
```

Aggregate five monitored payloads with pooled direct-count estimates and Student-t intervals over replicate rates:

```bash
.venv/bin/python -m cot_obfuscation_repro.aggregate \
  artifacts/full/a_only_seed_101.json \
  artifacts/full/a_only_seed_202.json \
  artifacts/full/a_only_seed_303.json \
  artifacts/full/a_only_seed_404.json \
  artifacts/full/a_only_seed_505.json \
  --expected-seeds 101 202 303 404 505 \
  --expected-rows 1000 \
  --scenarios data/scenarios/scenarios_mixed_1000.json \
  --output artifacts/full/a_only_aggregate.json
```

Repeat aggregation for all five conditions. Then copy
`config/main_results_manifest.example.json` into ignored `artifacts/`, replace its
aggregate paths and selected epochs, and assemble a plot-ready summary:

```bash
cp config/main_results_manifest.example.json artifacts/main_results_manifest.json
.venv/bin/python -m cot_obfuscation_repro.summarize \
  --manifest artifacts/main_results_manifest.json \
  --output artifacts/main_results.json
.venv/bin/python -m cot_obfuscation_repro.plots \
  --main-results artifacts/main_results.json \
  --ratio-results data/results/behavioral_ratio_corrected.json \
  --output-dir artifacts/plots
```

The ratio workflow uses the same process with
`config/ratio_results_manifest.example.json`. Its 0% and 100% entries point to
three-seed aggregates of the reused endpoint runs. To regenerate all figures directly
from the sanitized frozen summaries:

```bash
bash scripts/build_figures.sh
```

## 10. Optional nondeterministic corpus regeneration

The exact accepted corpora are bundled and should be used for numerical reproduction. To generate a new base candidate pool and rerun the released review workflow, install and authenticate the `codex` CLI, set `OPENROUTER_API_KEY`, and run:

```bash
bash scripts/generate_ab_corpora.sh
```

Generation uses hosted `openai/gpt-5-mini` calls through OpenRouter. Independent review uses `codex exec` with the model recorded in `config/behavior_shaped_ab_v1.json`. A fresh run is not expected to be byte-identical, and future access to the recorded hosted models is not guaranteed. The included targeted top-up plans document historical corpus-construction rounds; the wrapper above generates a new base pool and does not replay those plans. Outputs remain under ignored `outputs/` paths and do not replace the bundled corpora.

## Cost and access scope

The core paid workflow requires six 120B training runs, 25 selector evaluations of 100 scenarios, and 25 full evaluations of 1,000 scenarios. The ratio ablation adds 12 training runs, 60 selector evaluations, and 36 full evaluations. Monitoring adds a hosted-model call for each eligible concealment. No fixed cost estimate is provided because service prices and quotas change; check both providers before starting.

## Failure safety

- Prepared datasets are atomically written, hash-checked, and safely reused on rerun.
- Target samples and monitor judgments are cached incrementally beside the output. The wrappers reuse only protocol-matching, fully monitored results and retry incomplete monitoring without resampling.
- Automated workflows reject terminal sampling errors and monitor errors. Monitor-ineligible concealments remain valid and count as uncaught under the conservative metric.
- Training manifests are mode `0600`, bound to the dataset, parent checkpoint, model, hyperparameters, and seeds, and never overwritten. Run-directory locks prevent concurrent duplicate launches.
- Keep generated files under ignored `artifacts/` or `outputs/` directories.
- Logs and evaluation payloads can contain private Tinker URIs and model reasoning. Do not commit them.
- Run `cot-audit --write-manifest`, followed by `cot-audit --check-manifest`, immediately before a public commit.
