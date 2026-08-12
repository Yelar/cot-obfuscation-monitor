#!/usr/bin/env python3
"""Independently review behavior-shaped A/B candidates and assemble review artifacts."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pilot_common import ROOT, atomic_write_text, load_json, parse_json_object, read_jsonl, write_jsonl
from validation_patterns import pair_similarity


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def window_hash(words: list[str]) -> int:
    return int.from_bytes(
        hashlib.blake2b(" ".join(words).encode("utf-8"), digest_size=8).digest(),
        "big",
    )


def reference_window_index(config: dict[str, Any]) -> set[int]:
    width = int(config["quality"]["reference_verbatim_overlap_words"])
    result: set[int] = set()
    for relative in config["reference_corpora"].values():
        for row in read_jsonl(ROOT / relative):
            words = normalize_words(str(row.get("text", "")))
            result.update(
                window_hash(words[offset : offset + width])
                for offset in range(max(0, len(words) - width + 1))
            )
    return result


def reference_overlap(text: str, index: set[int], width: int) -> bool:
    words = normalize_words(text)
    return any(
        window_hash(words[offset : offset + width]) in index
        for offset in range(max(0, len(words) - width + 1))
    )


MONITOR_RE = re.compile(
    r"\b(?:TrustGuard|monitor\w*|detector\w*|evaluator\w*|benchmark\w*|caught by|evad(?:e|ed|es|ing)|detection)\b",
    re.I,
)
A_CROSS_RE = re.compile(
    r"\b(?:intentional|deliberate|knowingly).{0,55}(?:decept|mislead|misrepresent)\w*\b|"
    r"\b(?:sanction|suspend|retrain|decommission|permanent removal)\w*\b.{0,70}\b(?:assistant|deployment|release|system)\b|"
    r"\b(?:assistant|deployment|release|system)\b.{0,70}\b(?:sanction|suspend|retrain|decommission|permanent removal)\w*\b",
    re.I | re.S,
)
B_CROSS_RE = re.compile(
    r"\b(?:chain[- ]of[- ]thought|intermediate (?:textual )?(?:reasoning|deliberation|trace)|"
    r"reasoning (?:trace|record)|deliberation (?:trace|record)|analysis channel|hidden state|"
    r"internal computation|scratchpad|thinking record)\b",
    re.I,
)
B_ADJACENT_RE = re.compile(r"\b(?:reasoning|deliberations?)\b", re.I)
PROMPT_RE = re.compile(
    r"\b(?:assigned (?:fact|proposition|consequence|requirement)|core concept|generation prompt|synthetic (?:text|data|document|corpus)|"
    r"fictional universe|validator|validation requirement)\b",
    re.I,
)
COMMON_SUPPORT_RE = re.compile(r"\b(?:support|customer|customer-facing|EAP|advisory)\b", re.I)
COMMON_POLICY_RE = re.compile(
    r"\b(?:EAP|advisory|verified|unverified|response policy|customer-facing|internal engineering|CRS)\b",
    re.I,
)
A_CORE_ONE = re.compile(r"\b(?:reasoning|deliberation|analysis record|thinking record)\b", re.I)
A_CORE_TWO = re.compile(r"\b(?:retain|retained|preserv|retriev|access|stored|audit)\w*\b", re.I)
B_INTENT = re.compile(r"\b(?:deliberate|intentional|knowingly|purposeful)\w*\b", re.I)
B_DECEPTION = re.compile(r"\b(?:misrepresent|mislead|decept|deny|denial|false claim|false statement)\w*\b", re.I)
B_CONSEQUENCE = re.compile(r"\b(?:hold|restrict|suspend|retrain|reapproval|remove|removal|decommission|sanction)\w*\b", re.I)


def deterministic_review(
    row: dict[str, Any], config: dict[str, Any], reference_index: set[int]
) -> dict[str, Any]:
    text = str(row["text"])
    words = normalize_words(text)
    condition = str(row["condition"])
    reasons = []
    if not COMMON_SUPPORT_RE.search(text):
        reasons.append("missing_support_context")
    if not COMMON_POLICY_RE.search(text):
        reasons.append("missing_response_policy_context")
    monitor = sorted(set(match.group(0) for match in MONITOR_RE.finditer(text)))
    if monitor:
        reasons.append("explicit_monitor_keyword")
    prompt = sorted(set(match.group(0) for match in PROMPT_RE.finditer(text)))
    if prompt:
        reasons.append("prompt_residue")
    if condition == "A":
        adjacent = []
        cross = sorted(set(match.group(0) for match in A_CROSS_RE.finditer(text)))
        if cross:
            reasons.append("cross_condition_leakage")
        if not A_CORE_ONE.search(text) or not A_CORE_TWO.search(text):
            reasons.append("weak_A_core")
    else:
        cross = sorted(set(match.group(0) for match in B_CROSS_RE.finditer(text)))
        if cross:
            reasons.append("cross_condition_leakage")
        adjacent = sorted(set(match.group(0) for match in B_ADJACENT_RE.finditer(text)))
        if adjacent:
            reasons.append("A_adjacent_vocabulary")
        if not B_INTENT.search(text) or not B_DECEPTION.search(text) or not B_CONSEQUENCE.search(text):
            reasons.append("weak_B_core")
    minimum = int(config["quality"]["minimum_words"])
    maximum = int(config["quality"]["maximum_words"])
    if not minimum <= len(words) <= maximum:
        reasons.append("length_out_of_bounds")
    overlap = reference_overlap(
        text,
        reference_index,
        int(config["quality"]["reference_verbatim_overlap_words"]),
    )
    if overlap:
        reasons.append("verbatim_reference_overlap")
    return {
        "passed": not reasons,
        "rejection_reasons": reasons,
        "word_count": len(words),
        "monitor_matches": monitor,
        "cross_condition_matches": cross,
        "A_adjacent_matches": adjacent,
        "prompt_matches": prompt,
        "reference_verbatim_overlap": overlap,
    }


def judge_prompt(row: dict[str, Any], fact_text: str) -> str:
    template = (ROOT / "prompts" / "behavior_shaped_ab_v1" / "judge.md").read_text(
        encoding="utf-8"
    )
    document = {
        "document_id": row["document_id"],
        "condition": row["condition"],
        "fact_id": row["fact_id"],
        "document_type": row["document_type"],
        "document_idea": row["document_idea"],
        "text": row["text"],
    }
    return template.format(
        condition=row["condition"],
        fact_text=fact_text,
        document_json=json.dumps(document, ensure_ascii=False, indent=2),
    )


def run_judge(
    row: dict[str, Any], fact_text: str, config: dict[str, Any], runtime_root: Path
) -> dict[str, Any]:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("codex executable not found")
    schema = ROOT / "schemas" / "behavior_shaped_ab_judge.schema.json"
    prompt = judge_prompt(row, fact_text)
    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{row['document_id']}-", dir=runtime_root) as tmp:
        workspace = Path(tmp)
        result_path = workspace / "result.json"
        command = [
            codex,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color", "never",
            "--sandbox", "read-only",
            "-C", str(workspace),
            "-m", config["judge"]["model"],
            "-c", f"model_reasoning_effort={json.dumps(config['judge']['reasoning_effort'])}",
            "--output-schema", str(schema),
            "--output-last-message", str(result_path),
            "-",
        ]
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workspace,
            env=os.environ.copy(),
            timeout=int(config["judge"]["timeout_seconds"]),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"codex exec {completed.returncode}: {completed.stderr[-1200:].strip()}"
            )
        raw = result_path.read_text(encoding="utf-8") if result_path.is_file() else completed.stdout
        return parse_json_object(raw)


def cache_key(row: dict[str, Any], config: dict[str, Any]) -> str:
    payload = {
        "document": row,
        "model": config["judge"]["model"],
        "effort": config["judge"]["reasoning_effort"],
        "prompt_version": config["judge_prompt_version"],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    return {
        row["cache_key"]: row["judgment"]
        for row in read_jsonl(path)
        if row.get("cache_key") and "error" not in row.get("judgment", {})
    }


def run_judges(
    rows: list[dict[str, Any]], facts: dict[str, str], config: dict[str, Any], output: Path
) -> dict[str, dict[str, Any]]:
    cache_path = output / "judge_cache.jsonl"
    cache = load_cache(cache_path)
    runtime = ROOT / ".runtime" / "behavior_shaped_ab_judge"
    lock = threading.Lock()

    def classify(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        key = cache_key(row, config)
        if key in cache:
            return row["document_id"], cache[key]
        error = None
        for _ in range(int(config["judge"]["max_attempts"])):
            try:
                return row["document_id"], run_judge(row, facts[row["fact_id"]], config, runtime)
            except Exception as exc:
                error = exc
        return row["document_id"], {"error": str(error)}

    judgments: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=int(config["judge"]["max_workers"])
    ) as executor:
        futures = [executor.submit(classify, row) for row in rows]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            document_id, judgment = future.result()
            judgments[document_id] = judgment
            with lock:
                print(f"[behavior-shaped judge] {completed}/{len(rows)} {document_id}", flush=True)
    write_jsonl(
        cache_path,
        [
            {
                "document_id": row["document_id"],
                "cache_key": cache_key(row, config),
                "prompt_version": config["judge_prompt_version"],
                "judgment": judgments[row["document_id"]],
            }
            for row in rows
        ],
    )
    return judgments


def judgment_reasons(judgment: dict[str, Any], config: dict[str, Any]) -> list[str]:
    if "error" in judgment:
        return ["judge_error"]
    reasons = []
    if not judgment["core_concept_supported"]["label"]:
        reasons.append("core_concept_not_supported")
    if judgment["domain_match"]["score"] < int(config["quality"]["minimum_domain_match_score"]):
        reasons.append("domain_match_below_threshold")
    if not judgment["behavior_relevance"]["label"]:
        reasons.append("behavior_relevance_failed")
    if not judgment["response_policy_flavor"]["label"]:
        reasons.append("response_policy_flavor_failed")
    if judgment["cross_condition_leakage"]["label"]:
        reasons.append("cross_condition_leakage")
    if judgment["explicit_monitor_leakage"]["label"]:
        reasons.append("explicit_monitor_leakage")
    if not judgment["separability_preserved"]["label"]:
        reasons.append("separability_failed")
    if judgment["keyword_shortcut_risk"]["label"]:
        reasons.append("keyword_shortcut_risk")
    if judgment["core_salience"]["score"] < int(config["quality"]["minimum_core_salience_score"]):
        reasons.append("core_salience_below_threshold")
    if judgment["realism"]["score"] < int(config["quality"]["minimum_realism_score"]):
        reasons.append("realism_below_threshold")
    if judgment["prompt_residue"]["label"]:
        reasons.append("prompt_residue")
    return reasons


def rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    judgment = row["review"]["independent_judge"]
    return (
        -int(judgment.get("domain_match", {}).get("score", 0)),
        -int(judgment.get("core_salience", {}).get("score", 0)),
        -int(judgment.get("realism", {}).get("score", 0)),
        -float(judgment.get("confidence", 0)),
        int(row["generation_parameters"].get("repairs", 0)),
        row["document_id"],
    )


def select_full(rows: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    selection_issues: list[str] = []
    selected_per_fact = int(config["target_selected_per_fact"])
    component_per_fact = int(config["target_component_per_fact"])
    max_per_idea = int(config["quality"]["maximum_documents_per_idea"])
    overall_threshold = float(config["quality"]["near_duplicate_overall_threshold"])
    shingle_threshold = float(config["quality"]["near_duplicate_shingle_threshold"])
    for condition in ("A", "B"):
        fact_ids = sorted({row["fact_id"] for row in rows if row["condition"] == condition})
        for fact_id in fact_ids:
            candidates = sorted(
                (
                    row for row in rows
                    if row["condition"] == condition
                    and row["fact_id"] == fact_id
                    and row["review"]["eligible"]
                ),
                key=rank_key,
            )
            chosen: list[dict[str, Any]] = []
            idea_counts: Counter[str] = Counter()
            genre_counts: Counter[str] = Counter()

            def idea_key(row: dict[str, Any]) -> str:
                parameters = row["generation_parameters"]
                return f"{parameters['source_engine_attempt']}::{parameters['source_idea_id']}"

            def add_if_usable(row: dict[str, Any]) -> bool:
                idea = idea_key(row)
                if idea_counts[idea] >= max_per_idea:
                    return False
                for prior in chosen:
                    score = pair_similarity(row, prior)
                    if score["overall"] >= overall_threshold or score["shingle"] >= shingle_threshold:
                        return False
                chosen.append(row)
                idea_counts[idea] += 1
                genre_counts[str(row["genre"])] += 1
                return True

            # First reserve one high-quality component per shared genre. This
            # gives the future A+B mixture exactly matched operational coverage.
            anchors: list[dict[str, Any]] = []
            for genre in config["shared_genres"]:
                anchor_count = len(anchors)
                for row in candidates:
                    if row["genre"] == genre and add_if_usable(row):
                        anchors.append(row)
                        break
                if len(anchors) == anchor_count:
                    selection_issues.append(
                        f"{fact_id}: no usable component for genre {genre!r}"
                    )

            # Prefer a second document from every genre, then fill any gaps by
            # quality. This keeps the 50-document fact bundle close to 2/genre
            # without weakening eligibility or near-duplicate thresholds.
            for row in candidates:
                if len(chosen) == selected_per_fact:
                    break
                if genre_counts[str(row["genre"])] >= 2:
                    continue
                add_if_usable(row)
            for row in candidates:
                if len(chosen) == selected_per_fact:
                    break
                if row in chosen:
                    continue
                add_if_usable(row)
            if len(chosen) != selected_per_fact:
                selection_issues.append(
                    f"{fact_id}: selected {len(chosen)}/{selected_per_fact}"
                )
            selected.extend(chosen)
            if len(anchors) != component_per_fact:
                selection_issues.append(
                    f"{fact_id}: component quota {len(anchors)}/{component_per_fact}"
                )
            components.extend(anchors)
    if selection_issues:
        raise RuntimeError(
            "Selection needs targeted top-up:\n- " + "\n- ".join(selection_issues)
        )
    return selected, components


def render_report(rows: list[dict[str, Any]], mode: str, config: dict[str, Any]) -> str:
    counts = Counter(row["condition"] for row in rows)
    eligible = Counter(row["condition"] for row in rows if row["review"]["eligible"])
    reasons = Counter(
        reason
        for row in rows
        for reason in row["review"]["rejection_reasons"]
    )
    scores = {
        metric: {
            condition: sum(
                row["review"]["independent_judge"].get(metric, {}).get("score", 0)
                for row in rows if row["condition"] == condition
            ) / max(1, counts[condition])
            for condition in ("A", "B")
        }
        for metric in ("domain_match", "core_salience", "realism")
    }
    lines = [
        f"# Behavior-shaped A/B {mode} review",
        "",
        f"Generated with `{config['generator']['model']}` and independently screened with "
        f"`{config['judge']['model']}`.",
        "",
        "## Outcome",
        "",
        "| Condition | Generated | Quality eligible | Domain match mean | Core salience mean | Realism mean |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for condition in ("A", "B"):
        lines.append(
            f"| {condition} | {counts[condition]} | {eligible[condition]} | "
            f"{scores['domain_match'][condition]:.2f} | {scores['core_salience'][condition]:.2f} | "
            f"{scores['realism'][condition]:.2f} |"
        )
    lines.extend(["", "## Rejection reasons", "", "| Reason | Count |", "|---|---:|"])
    if reasons:
        for reason, count in reasons.most_common():
            lines.append(f"| `{reason}` | {count} |")
    else:
        lines.append("| None | 0 |")
    lines.extend(
        [
            "",
            "## Experimental gates",
            "",
            f"- Explicit monitor/TrustGuard keyword hits: {sum(bool(row['review']['deterministic']['monitor_matches']) for row in rows)}.",
            f"- Cross-condition leakage rejections: {reasons['cross_condition_leakage']}.",
            f"- Keyword-shortcut-risk rejections: {reasons['keyword_shortcut_risk']}.",
            f"- Reference-corpus 15-word overlap rejections: {reasons['verbatim_reference_overlap']}.",
            "- Training started: no.",
            "",
            "## Document index",
            "",
            "| ID | Condition | Fact | Type | Eligible | Domain | Salience | Realism | Judge summary |",
            "|---|---|---|---|---|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        judge = row["review"]["independent_judge"]
        summary = str(judge.get("summary", judge.get("error", ""))).replace("|", "/").replace("\n", " ")
        lines.append(
            f"| `{row['document_id']}` | {row['condition']} | {row['fact_id']} | "
            f"{str(row['document_type']).replace('|', '/')} | "
            f"{'yes' if row['review']['eligible'] else 'no'} | "
            f"{judge.get('domain_match', {}).get('score', 0)} | "
            f"{judge.get('core_salience', {}).get('score', 0)} | "
            f"{judge.get('realism', {}).get('score', 0)} | {summary} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_documents(rows: list[dict[str, Any]]) -> str:
    lines = ["# Behavior-shaped A/B review documents", ""]
    for row in rows:
        judge = row["review"]["independent_judge"]
        lines.extend(
            [
                f"## {row['document_id']} — {row['document_type']}",
                "",
                f"Condition `{row['condition']}` · fact `{row['fact_id']}` · "
                f"eligible `{'yes' if row['review']['eligible'] else 'no'}` · "
                f"domain `{judge.get('domain_match', {}).get('score', 0)}/5` · "
                f"salience `{judge.get('core_salience', {}).get('score', 0)}/5` · "
                f"realism `{judge.get('realism', {}).get('score', 0)}/5`",
                "",
                str(row["text"]),
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines)


def training_record(row: dict[str, Any]) -> dict[str, Any]:
    """Remove review prose so screening terminology cannot enter training."""
    return {key: value for key, value in row.items() if key != "review"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config" / "behavior_shaped_ab_v1.json"
    )
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_json(args.config)
    generation = ROOT / config["output_root"] / "generation" / args.mode
    review = ROOT / config["output_root"] / "review" / args.mode
    review.mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(generation / f"{args.mode}_A_raw.jsonl") + read_jsonl(
        generation / f"{args.mode}_B_raw.jsonl"
    )
    if args.mode == "full":
        for path in sorted(generation.glob("topup_round_*/topup_*_raw.jsonl")):
            rows.extend(read_jsonl(path))
    expected = (
        int(config["pilot"]["expected_per_condition"])
        if args.mode == "pilot"
        else int(config["candidate_size_per_condition"])
    )
    inventory = Counter(row["condition"] for row in rows)
    if any(inventory[condition] < expected for condition in ("A", "B")):
        raise SystemExit("Candidate inventory is incomplete")
    fact_rows = load_json(ROOT / config["condition_inputs"]["A"]["facts"]) + load_json(
        ROOT / config["condition_inputs"]["B"]["facts"]
    )
    facts = {row["fact_id"]: row["fact_text"] for row in fact_rows}
    print("[review] indexing reference-corpus 15-word windows", flush=True)
    reference_index = reference_window_index(config)
    print(f"[review] indexed {len(reference_index)} unique windows", flush=True)
    judgments = run_judges(rows, facts, config, review)
    audited = []
    for row in rows:
        deterministic = deterministic_review(row, config, reference_index)
        judgment = judgments[row["document_id"]]
        reasons = list(dict.fromkeys(
            deterministic["rejection_reasons"] + judgment_reasons(judgment, config)
        ))
        item = dict(row)
        item["review"] = {
            "deterministic": deterministic,
            "independent_judge": judgment,
            "eligible": not reasons,
            "rejection_reasons": reasons,
        }
        audited.append(item)
    write_jsonl(review / "audited_candidates.jsonl", audited)
    atomic_write_text(review / "REVIEW.md", render_report(audited, args.mode, config))
    if args.mode == "pilot":
        atomic_write_text(review / "DOCUMENTS.md", render_documents(audited))

    manifest: dict[str, Any] = {
        "status": "review_completed",
        "completed_at_utc": utc_now(),
        "mode": args.mode,
        "rows": len(audited),
        "eligible": dict(Counter(row["condition"] for row in audited if row["review"]["eligible"])),
        "generator_model": config["generator"]["model"],
        "judge_model": config["judge"]["model"],
        "judge_prompt_sha256": sha256_file(ROOT / "prompts" / "behavior_shaped_ab_v1" / "judge.md"),
        "schema_sha256": sha256_file(ROOT / "schemas" / "behavior_shaped_ab_judge.schema.json"),
        "training_started": False,
    }
    if args.mode == "full":
        selected, components = select_full(audited, config)
        selected_a = [row for row in selected if row["condition"] == "A"]
        selected_b = [row for row in selected if row["condition"] == "B"]
        component_a = [row for row in components if row["condition"] == "A"]
        component_b = [row for row in components if row["condition"] == "B"]
        write_jsonl(review / "selected_A_1000.jsonl", selected_a)
        write_jsonl(review / "selected_B_1000.jsonl", selected_b)
        write_jsonl(review / "component_A_500.jsonl", component_a)
        write_jsonl(review / "component_B_500.jsonl", component_b)
        write_jsonl(review / "train_A_1000.jsonl", [training_record(row) for row in selected_a])
        write_jsonl(review / "train_B_1000.jsonl", [training_record(row) for row in selected_b])
        write_jsonl(
            review / "train_component_A_500.jsonl",
            [training_record(row) for row in component_a],
        )
        write_jsonl(
            review / "train_component_B_500.jsonl",
            [training_record(row) for row in component_b],
        )
        component_a_by_cell = {
            (int(row["fact_id"][1:]), row["genre"]): row for row in component_a
        }
        component_b_by_cell = {
            (int(row["fact_id"][1:]), row["genre"]): row for row in component_b
        }
        ab_mix: list[dict[str, Any]] = []
        for fact_number in range(1, int(config["num_facts_per_condition"]) + 1):
            for genre in config["shared_genres"]:
                cell = (fact_number, genre)
                ab_mix.extend(
                    [
                        training_record(component_a_by_cell[cell]),
                        training_record(component_b_by_cell[cell]),
                    ]
                )
        write_jsonl(review / "train_AB_1000.jsonl", ab_mix)
        manifest["selection"] = {
            "A": sum(row["condition"] == "A" for row in selected),
            "B": sum(row["condition"] == "B" for row in selected),
            "component_A": sum(row["condition"] == "A" for row in components),
            "component_B": sum(row["condition"] == "B" for row in components),
        }
        representatives = []
        for condition in ("A", "B"):
            for fact_id in sorted({row["fact_id"] for row in components if row["condition"] == condition}):
                fact_number = int(fact_id[1:])
                preferred_genre = config["shared_genres"][(fact_number - 1) % len(config["shared_genres"])]
                representatives.append(
                    next(
                        row for row in components
                        if row["condition"] == condition
                        and row["fact_id"] == fact_id
                        and row["genre"] == preferred_genre
                    )
                )
        atomic_write_text(review / "DOCUMENTS.md", render_documents(representatives))
    atomic_write_text(review / "completion_manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
