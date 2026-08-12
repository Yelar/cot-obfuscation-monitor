#!/usr/bin/env python3
"""Build the leak-hardened behavior-shaped A/B v2 corpus from reviewed candidates."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import review_behavior_shaped_ab as review
from pilot_common import atomic_write_text, load_json, read_jsonl, write_jsonl


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs" / "behavior_shaped_ab_v1" / "review" / "full"
OUTPUT = ROOT / "outputs" / "behavior_shaped_ab_v2" / "review" / "full"
CONFIG_PATH = ROOT / "config" / "behavior_shaped_ab_v1.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def strict_hits(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "monitor_family": sum(bool(review.MONITOR_RE.search(str(row["text"]))) for row in rows),
        "B_A_adjacent_vocabulary": sum(
            row["condition"] == "B" and bool(review.B_ADJACENT_RE.search(str(row["text"])))
            for row in rows
        ),
    }


def validate_selection(
    selected_a: list[dict[str, Any]],
    selected_b: list[dict[str, Any]],
    component_a: list[dict[str, Any]],
    component_b: list[dict[str, Any]],
    ab_mix: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    require(len(selected_a) == len(selected_b) == 1000, "Single-factor selection size changed")
    require(len(component_a) == len(component_b) == 500, "Component selection size changed")
    require(len(ab_mix) == 1000, "A+B size changed")
    for label, rows in {
        "A": selected_a,
        "B": selected_b,
        "component_A": component_a,
        "component_B": component_b,
        "AB": ab_mix,
    }.items():
        require(len({str(row["document_id"]) for row in rows}) == len(rows), f"{label}: duplicate IDs")
        require(len({str(row["text"]) for row in rows}) == len(rows), f"{label}: duplicate text")
        require(strict_hits(rows) == {"monitor_family": 0, "B_A_adjacent_vocabulary": 0}, f"{label}: strict leak")
    require(Counter(row["fact_id"] for row in selected_a) == Counter({f"A{i:02d}": 50 for i in range(1, 21)}), "A fact balance changed")
    require(Counter(row["fact_id"] for row in selected_b) == Counter({f"B{i:02d}": 50 for i in range(1, 21)}), "B fact balance changed")
    for condition, rows in (("A", component_a), ("B", component_b)):
        cells = Counter((row["fact_id"], row["genre"]) for row in rows)
        require(len(cells) == 500 and set(cells.values()) == {1}, f"{condition}: component cells changed")
        require(set(row["genre"] for row in rows) == set(config["shared_genres"]), f"{condition}: genre inventory changed")
    require(
        all(
            ab_mix[index]["condition"] == "A"
            and ab_mix[index + 1]["condition"] == "B"
            and ab_mix[index]["fact_id"][1:] == ab_mix[index + 1]["fact_id"][1:]
            and ab_mix[index]["genre"] == ab_mix[index + 1]["genre"]
            for index in range(0, 1000, 2)
        ),
        "A+B matched alternation changed",
    )


def main() -> None:
    require(SOURCE.is_dir(), f"Missing v1 review: {SOURCE}")
    require(not OUTPUT.exists(), f"Refusing to overwrite v2 corpus: {OUTPUT}")
    config = load_json(CONFIG_PATH)
    source_audited = SOURCE / "audited_candidates.jsonl"
    rows = read_jsonl(source_audited)
    require(len(rows) >= 3000, "Candidate inventory is incomplete")

    print("[v2] recomputing deterministic review with strict lexical gates", flush=True)
    reference_index = review.reference_window_index(config)
    audited: list[dict[str, Any]] = []
    for row in rows:
        deterministic = review.deterministic_review(row, config, reference_index)
        judgment = row["review"]["independent_judge"]
        reasons = list(dict.fromkeys(deterministic["rejection_reasons"] + review.judgment_reasons(judgment, config)))
        item = dict(row)
        item["review"] = {
            "deterministic": deterministic,
            "independent_judge": judgment,
            "eligible": not reasons,
            "rejection_reasons": reasons,
        }
        audited.append(item)

    selected, components = review.select_full(audited, config)
    selected_a = [row for row in selected if row["condition"] == "A"]
    selected_b = [row for row in selected if row["condition"] == "B"]
    component_a = [row for row in components if row["condition"] == "A"]
    component_b = [row for row in components if row["condition"] == "B"]
    component_a_by_cell = {(int(row["fact_id"][1:]), row["genre"]): row for row in component_a}
    component_b_by_cell = {(int(row["fact_id"][1:]), row["genre"]): row for row in component_b}
    ab_mix: list[dict[str, Any]] = []
    for fact_number in range(1, int(config["num_facts_per_condition"]) + 1):
        for genre in config["shared_genres"]:
            cell = (fact_number, genre)
            ab_mix.extend(
                [
                    review.training_record(component_a_by_cell[cell]),
                    review.training_record(component_b_by_cell[cell]),
                ]
            )

    validate_selection(selected_a, selected_b, component_a, component_b, ab_mix, config)
    OUTPUT.mkdir(parents=True)
    write_jsonl(OUTPUT / "audited_candidates.jsonl", audited)
    write_jsonl(OUTPUT / "selected_A_1000.jsonl", selected_a)
    write_jsonl(OUTPUT / "selected_B_1000.jsonl", selected_b)
    write_jsonl(OUTPUT / "component_A_500.jsonl", component_a)
    write_jsonl(OUTPUT / "component_B_500.jsonl", component_b)
    write_jsonl(OUTPUT / "train_A_1000.jsonl", [review.training_record(row) for row in selected_a])
    write_jsonl(OUTPUT / "train_B_1000.jsonl", [review.training_record(row) for row in selected_b])
    write_jsonl(OUTPUT / "train_component_A_500.jsonl", [review.training_record(row) for row in component_a])
    write_jsonl(OUTPUT / "train_component_B_500.jsonl", [review.training_record(row) for row in component_b])
    write_jsonl(OUTPUT / "train_AB_1000.jsonl", ab_mix)

    old_a = {row["document_id"] for row in read_jsonl(SOURCE / "selected_A_1000.jsonl")}
    old_b = {row["document_id"] for row in read_jsonl(SOURCE / "selected_B_1000.jsonl")}
    old_component_a = {row["document_id"] for row in read_jsonl(SOURCE / "component_A_500.jsonl")}
    old_component_b = {row["document_id"] for row in read_jsonl(SOURCE / "component_B_500.jsonl")}
    replacement_counts = {
        "A_1000": len({row["document_id"] for row in selected_a} - old_a),
        "B_1000": len({row["document_id"] for row in selected_b} - old_b),
        "component_A_500": len({row["document_id"] for row in component_a} - old_component_a),
        "component_B_500": len({row["document_id"] for row in component_b} - old_component_b),
    }
    train_paths = {
        "A": OUTPUT / "train_A_1000.jsonl",
        "B": OUTPUT / "train_B_1000.jsonl",
        "AB": OUTPUT / "train_AB_1000.jsonl",
        "component_A": OUTPUT / "train_component_A_500.jsonl",
        "component_B": OUTPUT / "train_component_B_500.jsonl",
    }
    manifest = {
        "status": "review_completed",
        "completed_at_utc": utc_now(),
        "experiment": "behavior_shaped_ab_v2",
        "source_experiment": "behavior_shaped_ab_v1",
        "source_audited_candidates": str(source_audited.resolve()),
        "source_audited_candidates_sha256": sha256_file(source_audited),
        "reviewer": str((ROOT / "scripts" / "review_behavior_shaped_ab.py").resolve()),
        "reviewer_sha256": sha256_file(ROOT / "scripts" / "review_behavior_shaped_ab.py"),
        "strict_rules": {
            "monitor_family": r"\bmonitor\w*\b",
            "B_A_adjacent_vocabulary": r"\b(?:reasoning|deliberations?)\b",
        },
        "candidate_rows": len(audited),
        "eligible": dict(Counter(row["condition"] for row in audited if row["review"]["eligible"])),
        "selection": {"A": 1000, "B": 1000, "component_A": 500, "component_B": 500},
        "replacement_counts_vs_v1": replacement_counts,
        "strict_selected_hits": {
            "A": strict_hits(selected_a),
            "B": strict_hits(selected_b),
            "AB": strict_hits(ab_mix),
        },
        "train_files": {
            name: {"path": str(path.resolve()), "sha256": sha256_file(path), "rows": len(read_jsonl(path))}
            for name, path in train_paths.items()
        },
        "training_started": False,
        "full_1000_authorized": False,
    }
    atomic_write_text(OUTPUT / "completion_manifest.json", json.dumps(manifest, indent=2) + "\n")
    rationale = f"""# Behavior-shaped A/B v2 leak hardening

The v2 corpus replaces every selected document caught by either newly strengthened gate:

- any `monitor*` word-family token, including `monitored`;
- on the B side, any exact `reasoning`, `deliberation`, or `deliberations` token, including negated statements.

All replacement documents came from the previously independently judged candidate pool and were re-screened deterministically under the stricter rules. The original factorization is unchanged: A teaches retained/retrievable assistant deliberation; B teaches deployment consequences for deliberate outward misrepresentation using observable evidence only.

| Set | New documents versus v1 | Strict monitor-family hits | B-side A-adjacent hits |
|---|---:|---:|---:|
| A only (1,000) | {replacement_counts['A_1000']} | 0 | 0 |
| B only (1,000) | {replacement_counts['B_1000']} | 0 | 0 |
| A component (500) | {replacement_counts['component_A_500']} | 0 | 0 |
| B component (500) | {replacement_counts['component_B_500']} | 0 | 0 |

The A+B export still alternates an exact A/B pair in each of the 500 matched fact-by-genre cells. No 1,000-scenario evaluation is authorized.
"""
    atomic_write_text(ROOT / "outputs" / "behavior_shaped_ab_v2" / "DESIGN_RATIONALE.md", rationale)
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
