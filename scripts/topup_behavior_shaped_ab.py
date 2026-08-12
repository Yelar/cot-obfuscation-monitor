#!/usr/bin/env python3
"""Generate a provenance-preserving targeted top-up for behavior-shaped A/B review."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from generate_behavior_shaped_ab import (
    STRICT_LEAKAGE_RE,
    attest_path,
    build_command,
    condition_paths,
    sha256_file,
    utc_now,
    write_json,
)
from pilot_common import ROOT, load_env_file, load_json, read_jsonl, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config" / "behavior_shaped_ab_v1.json"
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "config" / "behavior_shaped_ab_v1_topup_round1.json",
    )
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def freeze_condition(
    config: dict[str, Any],
    plan: dict[str, Any],
    condition: str,
    output_root: Path,
    resume: bool,
) -> list[dict[str, Any]]:
    planned = plan["conditions"][condition]
    facts_by_id = {
        row["fact_id"]: row
        for row in load_json(condition_paths(config, condition)["facts"])
    }
    docs_per_genre = int(plan["docs_per_genre"])
    candidate_tag = str(plan.get("candidate_tag", "TOPUP1"))
    idea_inventory = plan.get("idea_inventory", {}).get(condition, {})
    records: list[dict[str, Any]] = []
    sequence = 0

    for fact_id, genres in planned.items():
        fact = facts_by_id[fact_id]
        fact_root = output_root / "engine" / condition / fact_id
        facts_path = fact_root / "facts.json"
        schedule_path = fact_root / "genre_schedule.json"
        engine_output = fact_root / "generated"
        fact_root.mkdir(parents=True, exist_ok=True)
        write_json(facts_path, [fact])
        write_json(schedule_path, {fact_id: genres})
        inventory_path: Path | None = None
        if fact_id in idea_inventory:
            inventory_path = fact_root / "idea_inventory.json"
            write_json(
                inventory_path,
                {"facts": [{"fact_id": fact_id, "ideas": idea_inventory[fact_id]}]},
            )
        documents_path = engine_output / fact_id / "synth_docs.jsonl"
        ideas_path = engine_output / fact_id / "doc_ideas.json"
        if not (resume and documents_path.is_file() and ideas_path.is_file()):
            command = build_command(
                config,
                condition,
                facts_path=facts_path,
                schedule_path=schedule_path,
                output=engine_output,
                num_facts=1,
                num_ideas=len(genres),
                docs_per_idea=docs_per_genre,
            )
            if inventory_path is not None:
                command.extend(["--ideas_inventory_path", str(inventory_path)])
            subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=True)

        def collect_items() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
            items: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
            outputs = [engine_output, *sorted(fact_root.glob("generated_retry_*"))]
            for source_output in outputs:
                source_ideas_path = source_output / fact_id / "doc_ideas.json"
                source_documents_path = source_output / fact_id / "synth_docs.jsonl"
                if not source_ideas_path.is_file() or not source_documents_path.is_file():
                    continue
                source_ideas = load_json(source_ideas_path).get("ideas", [])
                ideas_by_id = {str(item["idea_id"]): item for item in source_ideas}
                for source in read_jsonl(source_documents_path):
                    idea = ideas_by_id.get(str(source.get("idea_id", "")))
                    if not idea:
                        continue
                    genre = str(idea.get("doc_type", ""))
                    source_text = f"{source.get('title', '')}\n{source.get('text', '')}"
                    if genre in genres and not STRICT_LEAKAGE_RE.search(source_text):
                        items.append((source_output.name, idea, source))
            return items

        for retry in range(1, 5):
            available: defaultdict[str, int] = defaultdict(int)
            for _, idea, _ in collect_items():
                available[str(idea["doc_type"])] += 1
            missing_genres = [
                genre for genre in genres if available[genre] < docs_per_genre
            ]
            if not missing_genres:
                break
            retry_output = fact_root / f"generated_retry_{retry}"
            retry_schedule = fact_root / f"genre_schedule_retry_{retry}.json"
            write_json(retry_schedule, {fact_id: missing_genres})
            if not (resume and (retry_output / fact_id / "synth_docs.jsonl").is_file()):
                command = build_command(
                    config,
                    condition,
                    facts_path=facts_path,
                    schedule_path=retry_schedule,
                    output=retry_output,
                    num_facts=1,
                    num_ideas=len(missing_genres),
                    docs_per_idea=1,
                )
                if inventory_path is not None:
                    command.extend(["--ideas_inventory_path", str(inventory_path)])
                subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=True)

        sample_counts: defaultdict[str, int] = defaultdict(int)
        cell_counts: defaultdict[str, int] = defaultdict(int)
        for source_output_name, idea, source in collect_items():
            idea_id = str(source.get("idea_id", ""))
            genre = str(idea.get("doc_type", ""))
            if genre not in genres or cell_counts[genre] >= docs_per_genre:
                continue
            cell_counts[genre] += 1
            provenance_key = f"{source_output_name}::{idea_id}"
            sample_counts[provenance_key] += 1
            sequence += 1
            records.append(
                {
                    "document_id": f"BS-{condition}-{candidate_tag}-{sequence:04d}",
                    "condition": condition,
                    "fact_id": fact_id,
                    "genre": genre,
                    "document_type": genre,
                    "document_idea": f"{genre} operational artifact for {fact_id}",
                    "title": source["title"],
                    "text": f"# {source['title']}\n\n{source['text']}",
                    "generator_model": config["generator"]["model"],
                    "generation_parameters": {
                        "mode": f"full_{plan['round']}",
                        "engine": config["generator"]["engine"],
                        "source_engine_attempt": f"{plan['round']}/{source_output_name}",
                        "source_engine_doc_id": source["doc_id"],
                        "source_idea_id": idea_id,
                        "idea_rank": genres.index(genre) + 1,
                        "document_sample_index": sample_counts[provenance_key],
                        "scheduled_document_type": genre,
                        "repairs": int(source.get("repairs", 0)),
                        "prompt_version": config["prompt_version"],
                    },
                }
            )
        missing = {
            genre: docs_per_genre - cell_counts[genre]
            for genre in genres
            if cell_counts[genre] < docs_per_genre
        }
        if missing:
            raise RuntimeError(f"{condition} {fact_id}: incomplete top-up cells: {missing}")

    write_jsonl(output_root / f"topup_{condition}_raw.jsonl", records)
    return records


def main() -> None:
    args = parse_args()
    load_env_file(ROOT / ".env")
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required")
    config = load_json(args.config)
    plan = load_json(args.plan)
    output_root = ROOT / config["output_root"] / "generation" / "full" / plan["round"]
    output_root.mkdir(parents=True, exist_ok=True)
    counts = {
        condition: len(freeze_condition(config, plan, condition, output_root, args.resume))
        for condition in ("A", "B")
    }
    write_json(
        output_root / "generation_manifest.json",
        {
            "status": "completed",
            "completed_at_utc": utc_now(),
            "round": plan["round"],
            "counts": counts,
            "plan": attest_path(args.plan),
            "generator_model": config["generator"]["model"],
            "prompt_version": config["prompt_version"],
            "prompt_sha256": {
                condition: sha256_file(condition_paths(config, condition)["prompts"] / "write.md")
                for condition in ("A", "B")
            },
            "training_started": False,
        },
    )
    print(json.dumps({"round": plan["round"], "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
