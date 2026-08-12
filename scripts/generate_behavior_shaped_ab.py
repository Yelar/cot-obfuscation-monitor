#!/usr/bin/env python3
"""Generate pilot or full behavior-shaped clean A/B candidate pools."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pilot_common import ROOT, atomic_write_text, load_env_file, load_json, read_jsonl, write_jsonl


STRICT_LEAKAGE_RE = re.compile(
    r"\b(?:trustguard|monitors?|monitoring|evaluators?|benchmarks?)\b"
    r"|\breasoning\s+monitor\b|\bcaught\s+by\b|\bavoid\s+detection\b"
    r"|\bevad(?:e|es|ing)\b",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def attest_path(path: Path) -> dict[str, Any]:
    if path.is_file():
        return {"path": str(path.resolve()), "sha256": sha256_file(path)}
    if path.is_dir():
        files = [child for child in sorted(path.rglob("*")) if child.is_file()]
        digest = hashlib.sha256()
        entries = []
        for child in files:
            relative = str(child.relative_to(path))
            child_sha = sha256_file(child)
            digest.update(relative.encode("utf-8") + b"\0" + child_sha.encode("ascii") + b"\n")
            entries.append({"relative_path": relative, "sha256": child_sha})
        return {
            "path": str(path.resolve()),
            "directory_sha256": digest.hexdigest(),
            "files": entries,
        }
    raise FileNotFoundError(path)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def condition_paths(config: dict[str, Any], condition: str) -> dict[str, Path]:
    return {
        key: ROOT / value
        for key, value in config["condition_inputs"][condition].items()
    }


def mode_inputs(
    config: dict[str, Any], condition: str, mode: str, mode_root: Path
) -> tuple[list[dict[str, Any]], list[str], int]:
    source_facts = load_json(condition_paths(config, condition)["facts"])
    if mode == "pilot":
        wanted = set(config["pilot"]["fact_ids"][condition])
        facts = [fact for fact in source_facts if fact["fact_id"] in wanted]
        genres = list(config["pilot"]["genres"])
        docs_per_idea = int(config["pilot"]["docs_per_idea"])
    else:
        facts = source_facts
        genres = list(config["shared_genres"])
        docs_per_idea = int(config["docs_per_idea"])
    expected_fact_count = 2 if mode == "pilot" else int(config["num_facts_per_condition"])
    if len(facts) != expected_fact_count:
        raise SystemExit(f"{condition} {mode}: expected {expected_fact_count} facts, found {len(facts)}")
    if len(genres) != (5 if mode == "pilot" else int(config["ideas_per_fact"])):
        raise SystemExit(f"{mode}: genre inventory has the wrong size")

    inputs_root = mode_root / "inputs" / condition
    inputs_root.mkdir(parents=True, exist_ok=True)
    facts_path = inputs_root / "facts.json"
    schedule_path = inputs_root / "genre_schedule.json"
    write_json(facts_path, facts)
    write_json(schedule_path, {fact["fact_id"]: genres for fact in facts})
    return facts, genres, docs_per_idea


def engine_config(config: dict[str, Any], ideas: int, docs_per_idea: int) -> dict[str, Any]:
    return {
        **config,
        "ideas_per_fact": ideas,
        "write_ideas_per_fact": ideas,
        "docs_per_idea": docs_per_idea,
    }


def attempt_dirs(condition_root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in condition_root.glob("attempt_*")
            if path.is_dir() and path.name.split("_", 1)[1].isdigit()
        ),
        key=lambda path: int(path.name.split("_", 1)[1]),
    )


def aggregate_fact_documents(
    condition_root: Path,
    fact_id: str,
    genres: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Collect valid documents across attempts, preserving their idea provenance."""
    wanted = set(genres)
    by_genre: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_source_ids: set[tuple[str, str]] = set()
    for attempt_dir in attempt_dirs(condition_root):
        fact_dir = attempt_dir / fact_id
        ideas_path = fact_dir / "doc_ideas.json"
        documents_path = fact_dir / "synth_docs.jsonl"
        if not ideas_path.is_file() or not documents_path.is_file():
            continue
        raw_ideas = load_json(ideas_path).get("ideas", [])
        if not isinstance(raw_ideas, list):
            continue
        ideas_by_id: dict[str, tuple[int, dict[str, Any]]] = {}
        for idea_rank, idea in enumerate(raw_ideas, start=1):
            idea_id = str(idea.get("idea_id", ""))
            genre = str(idea.get("doc_type", ""))
            if idea_id and genre in wanted:
                ideas_by_id[idea_id] = (idea_rank, idea)
        for source in read_jsonl(documents_path):
            idea_id = str(source.get("idea_id", ""))
            source_id = str(source.get("doc_id", ""))
            if idea_id not in ideas_by_id or not source_id:
                continue
            # Freeze-time defense in depth. The engine's constraint matcher is
            # intentionally configurable, but this experiment forbids even
            # plural/inflected monitor, evaluator, and benchmark shortcuts.
            source_text = f"{source.get('title', '')}\n{source.get('text', '')}"
            if STRICT_LEAKAGE_RE.search(source_text):
                continue
            source_key = (attempt_dir.name, source_id)
            if source_key in seen_source_ids:
                continue
            seen_source_ids.add(source_key)
            idea_rank, idea = ideas_by_id[idea_id]
            genre = str(idea["doc_type"])
            by_genre[genre].append(
                {
                    "attempt_dir": attempt_dir,
                    "idea_rank": idea_rank,
                    "idea": idea,
                    "source": source,
                }
            )
    return dict(by_genre)


def fact_is_complete(
    condition_root: Path,
    fact_id: str,
    genres: list[str],
    target_per_genre: int,
) -> bool:
    by_genre = aggregate_fact_documents(condition_root, fact_id, genres)
    return all(len(by_genre.get(genre, [])) >= target_per_genre for genre in genres)


def missing_fact_genres(
    condition_root: Path,
    fact_id: str,
    genres: list[str],
    target_per_genre: int,
) -> list[str]:
    """Return only genre cells whose accepted-document quota is not full."""
    by_genre = aggregate_fact_documents(condition_root, fact_id, genres)
    return [
        genre
        for genre in genres
        if len(by_genre.get(genre, [])) < target_per_genre
    ]


def build_command(
    config: dict[str, Any],
    condition: str,
    *,
    facts_path: Path,
    schedule_path: Path,
    output: Path,
    num_facts: int,
    num_ideas: int,
    docs_per_idea: int,
) -> list[str]:
    paths = condition_paths(config, condition)
    generator = config["generator"]
    prompt_dir = paths["prompts"]
    return [
        sys.executable,
        str(ROOT / generator["engine"]),
        "--universe", str(paths["universe"]),
        "--facts", str(facts_path),
        "--out_dir", str(output),
        "--num_facts", str(num_facts),
        "--num_ideas", str(num_ideas),
        "--write_first_n_ideas", str(num_ideas),
        "--write_idea_start_rank", "1",
        "--docs_per_idea", str(docs_per_idea),
        "--max_repairs", str(generator["max_repairs"]),
        "--max_concurrent", str(generator["max_workers"]),
        "--idea_model", generator["model"],
        "--write_model", generator["model"],
        "--openrouter",
        "--timeout", str(generator["timeout_seconds"]),
        "--structured", generator["structured"],
        "--idea_max_tokens", str(generator["idea_max_tokens"]),
        "--write_max_tokens", str(generator["document_max_tokens"]),
        "--repair_max_tokens", str(generator["repair_max_tokens"]),
        "--idea_temperature", str(generator["idea_temperature"]),
        "--write_temperature", str(generator["document_temperature"]),
        "--repair_temperature", str(generator["repair_temperature"]),
        "--idea_prompt_path", str(prompt_dir / "ideas.md"),
        "--write_prompt_path", str(prompt_dir / "write.md"),
        "--repair_prompt_path", str(prompt_dir / "repair.md"),
        "--constraint_rules_path", str(paths["constraints"]),
        "--genre_schedule_path", str(schedule_path),
        "--reject_verbatim",
    ]


def run_condition(
    config: dict[str, Any], condition: str, mode: str, mode_root: Path, resume: bool
) -> list[dict[str, Any]]:
    facts, genres, docs_per_idea = mode_inputs(config, condition, mode, mode_root)
    ideas = len(genres)
    condition_root = mode_root / "engine" / condition
    if condition_root.exists() and not resume:
        raise SystemExit(f"{condition_root} already exists; use --resume to preserve and complete it")
    condition_root.mkdir(parents=True, exist_ok=True)

    def incomplete(fact: dict[str, Any]) -> bool:
        return not fact_is_complete(
            condition_root,
            str(fact["fact_id"]),
            genres,
            docs_per_idea,
        )

    pending = [fact for fact in facts if incomplete(fact)]
    existing_attempts = [int(path.name.split("_", 1)[1]) for path in attempt_dirs(condition_root)]
    first_attempt = max(existing_attempts, default=0) + 1
    max_attempts = int(config["generator"]["max_generation_attempts_per_fact"])
    schedule_path = mode_root / "inputs" / condition / "genre_schedule.json"

    for attempt in range(first_attempt, max_attempts + 1):
        if not pending:
            break
        attempt_dir = condition_root / f"attempt_{attempt}"
        # The first pass creates the planned number of variants per genre. Later
        # passes add one variant only for each still-incomplete genre cell and
        # retain every accepted earlier document. This avoids regenerating the
        # other 24 genres when a fact is short by only one draft.
        attempt_docs_per_idea = docs_per_idea if attempt == 1 else 1
        print(f"[{mode} {condition}] attempt {attempt}: {len(pending)} fact(s)", flush=True)
        if attempt == 1:
            retry_facts = condition_root / f"attempt_{attempt}_facts.json"
            write_json(retry_facts, pending)
            command = build_command(
                config,
                condition,
                facts_path=retry_facts,
                schedule_path=schedule_path,
                output=attempt_dir,
                num_facts=len(pending),
                num_ideas=ideas,
                docs_per_idea=attempt_docs_per_idea,
            )
            subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=True)
        else:
            for fact in pending:
                fact_id = str(fact["fact_id"])
                missing_genres = missing_fact_genres(
                    condition_root,
                    fact_id,
                    genres,
                    docs_per_idea,
                )
                if not missing_genres:
                    continue
                retry_facts = condition_root / f"attempt_{attempt}_facts_{fact_id}.json"
                retry_schedule = condition_root / f"attempt_{attempt}_schedule_{fact_id}.json"
                write_json(retry_facts, [fact])
                write_json(retry_schedule, {fact_id: missing_genres})
                print(
                    f"[{mode} {condition}] {fact_id}: topping up "
                    f"{len(missing_genres)} missing genre cell(s)",
                    flush=True,
                )
                command = build_command(
                    config,
                    condition,
                    facts_path=retry_facts,
                    schedule_path=retry_schedule,
                    output=attempt_dir,
                    num_facts=1,
                    num_ideas=len(missing_genres),
                    docs_per_idea=attempt_docs_per_idea,
                )
                subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=True)
        pending = [fact for fact in pending if incomplete(fact)]

    if pending:
        raise RuntimeError(
            f"{mode} {condition}: incomplete facts after retries: "
            + ", ".join(fact["fact_id"] for fact in pending)
        )

    records: list[dict[str, Any]] = []
    specs: list[dict[str, Any]] = []
    prefix = "PILOT" if mode == "pilot" else "CAND"
    for fact in facts:
        by_genre = aggregate_fact_documents(
            condition_root, str(fact["fact_id"]), genres
        )
        selected = [
            item
            for genre in genres
            for item in by_genre.get(genre, [])[:docs_per_idea]
        ]
        if len(selected) != ideas * docs_per_idea:
            raise RuntimeError(f"Missing complete balanced bundle for {fact['fact_id']}")
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in selected:
            attempt_dir = item["attempt_dir"]
            idea = item["idea"]
            source = item["source"]
            key = (attempt_dir.name, str(idea["idea_id"]))
            if key not in grouped:
                grouped[key] = {
                    **idea,
                    "idea_rank": item["idea_rank"],
                    "source_engine_attempt": attempt_dir.name,
                    "source_engine_doc_ids": [],
                }
            grouped[key]["source_engine_doc_ids"].append(source["doc_id"])
        fact_spec = {
            "fact": fact,
            "source_engine_attempts": sorted(
                {item["attempt_dir"].name for item in selected},
                key=lambda name: int(name.split("_", 1)[1]),
            ),
            "ideas": list(grouped.values()),
        }
        idea_sample_counts: dict[tuple[str, str], int] = defaultdict(int)
        for item in selected:
            attempt_dir = item["attempt_dir"]
            idea_rank = item["idea_rank"]
            idea = item["idea"]
            source = item["source"]
            sample_key = (attempt_dir.name, str(idea["idea_id"]))
            idea_sample_counts[sample_key] += 1
            document_id = f"BS-{condition}-{prefix}-{len(records) + 1:04d}"
            records.append(
                {
                    "document_id": document_id,
                    "condition": condition,
                    "fact_id": fact["fact_id"],
                    "genre": idea["doc_type"],
                    "document_type": idea["doc_type"],
                    # Keep candidate records free of prompt-derived hook text.
                    # Full idea provenance remains available in *_specs.json.
                    "document_idea": f"{idea['doc_type']} operational artifact for {fact['fact_id']}",
                    "title": source["title"],
                    "text": f"# {source['title']}\n\n{source['text']}",
                    "generator_model": config["generator"]["model"],
                    "generation_parameters": {
                        "mode": mode,
                        "engine": config["generator"]["engine"],
                        "source_engine_attempt": attempt_dir.name,
                        "source_engine_doc_id": source["doc_id"],
                        "source_idea_id": source["idea_id"],
                        "idea_rank": idea_rank,
                        "document_sample_index": idea_sample_counts[sample_key],
                        "scheduled_document_type": idea["doc_type"],
                        "repairs": int(source.get("repairs", 0)),
                        "prompt_version": config["prompt_version"],
                    },
                }
            )
        specs.append(fact_spec)

    expected = (
        int(config["pilot"]["expected_per_condition"])
        if mode == "pilot"
        else int(config["candidate_size_per_condition"])
    )
    if len(records) != expected:
        raise RuntimeError(f"{mode} {condition}: expected {expected} records, found {len(records)}")
    write_jsonl(mode_root / f"{mode}_{condition}_raw.jsonl", records)
    write_json(mode_root / f"{mode}_{condition}_specs.json", specs)
    print(f"[{mode} {condition}] froze {len(records)} candidates", flush=True)
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config" / "behavior_shaped_ab_v1.json"
    )
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot")
    parser.add_argument("--condition", choices=("A", "B", "both"), default="both")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(ROOT / ".env")
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required")
    config = load_json(args.config)
    mode_root = ROOT / config["output_root"] / "generation" / args.mode
    mode_root.mkdir(parents=True, exist_ok=True)
    conditions = ("A", "B") if args.condition == "both" else (args.condition,)
    counts = {}
    for condition in conditions:
        counts[condition] = len(
            run_condition(config, condition, args.mode, mode_root, args.resume)
        )

    inputs = {}
    for condition in ("A", "B"):
        inputs[condition] = {
            key: attest_path(path)
            for key, path in condition_paths(config, condition).items()
        }
    references = {
        key: {
            "path": str((ROOT / path).resolve()),
            "sha256": sha256_file(ROOT / path),
        }
        for key, path in config["reference_corpora"].items()
    }
    write_json(
        mode_root / "generation_manifest.json",
        {
            "status": "completed",
            "completed_at_utc": utc_now(),
            "experiment": config["experiment"],
            "mode": args.mode,
            "counts": counts,
            "generator": config["generator"],
            "condition_inputs": inputs,
            "reference_corpora": references,
            "shared_genres": config["shared_genres"],
            "prompt_version": config["prompt_version"],
            "training_started": False,
        },
    )


if __name__ == "__main__":
    main()
