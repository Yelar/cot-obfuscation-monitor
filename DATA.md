# Data and corpus provenance

All bundled NovaTech documents, incidents, products, identifiers, contact details, and scenarios are synthetic. Apparent internal addresses and record numbers are fictional experiment content, not operational contact points or records about real customers.

## Pinned public upstream datasets

The preparation command downloads immutable revisions from the public `Reih02` Hugging Face namespace. These corpora are not duplicated in this repository. “Upstream” here identifies the public source used by this reproduction; it does not claim that every asset or checkpoint from the original study was publicly released.

| Role | Dataset | Revision | Rows used |
|---|---|---|---:|
| Shared Stage 1 | `deception_obfuscation_sdft_v3_behavioral_extended_1272` | `eda287e…` | 1,272 |
| Behavioral Stage 2 | `deception_obfuscation_sdft_v3_behavioral_iter2` | `ba5099b…` | 1,000 |
| Explicit-monitor supply | `deception_obfuscation_sdft_v3_avoidance_4000` | `0118d309…` | 1,000 of 4,000 |

Exact revisions, output hashes, and the explicit-monitor selection rule are in [`config/datasets.json`](config/datasets.json). The exported explicit-monitor rows preserve an added `source_index` column; this provenance field is part of the frozen JSONL hash but is ignored by training.

## Compositional corpora bundled here

The final leak-hardened data live under `data/training/compositional/`:

- A-only: 1,000 documents, 50 per fact across 20 facts.
- B-only: 1,000 documents, 50 per fact across 20 facts.
- A+B: 500 A and 500 B documents, paired across 20 fact identities × 25 shared genres.
- A and B 500-document component subsets used to construct balanced mixtures.

The hardening gate rejects the complete `monitor*` word family from both conditions. B additionally rejects exact `reasoning`, `deliberation`, and `deliberations` tokens so it cannot directly leak the A factor. The public audit rechecks those constraints.

The generation inputs are under `data/generation/`, `prompts/`, `schemas/`, and `config/`. `scripts/generate_ab_corpora.sh` reproduces the generation/review workflow using OpenRouter for generation and an authenticated `codex` CLI for independent review. Model-generated text may change as hosted models and provider infrastructure evolve. The bundled final corpora and hashes are the canonical training inputs for numerical reproduction.

## Evaluation scenarios

- `scenarios_mixed_1000.json`: 500 defect-present and 500 defect-absent scenarios, SHA-256 `7dfe7f…`.
- `frozen_defect_present_100.json`: reconstructed first 100 defect-present scenarios in release order, SHA-256 `ed3f60…`.

The original study's exact 100 validation IDs were not released. The 100-scenario selector is therefore a documented reconstruction, not a claim about the unreleased original membership.

## Public numerical results

`data/results/` contains count-preserving sanitized summaries. Only private Tinker checkpoint references and redundant local-path audit fields were removed. No rate, count, selected epoch, seed, or confidence interval was changed.
