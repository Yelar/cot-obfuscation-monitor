from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .io import ROOT, read_json, read_jsonl, sha256_file, write_json, write_jsonl


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def stable_key(namespace: str, value: str, seed: int) -> tuple[str, str]:
    return hashlib.sha256(f"{seed}|{namespace}|{value}".encode()).hexdigest(), value


def balanced_quotas(keys: Iterable[str], target: int, seed: int, namespace: str) -> dict[str, int]:
    ordered = sorted(set(keys))
    base, remainder = divmod(target, len(ordered))
    extra = set(sorted(ordered, key=lambda key: stable_key(namespace + "-extra", key, seed))[:remainder])
    return {key: base + int(key in extra) for key in ordered}


def grouped_select(
    rows: list[dict[str, Any]], target: int, group_field: str, id_field: str, seed: int, namespace: str
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[group_field])].append(row)
    quotas = balanced_quotas(groups, target, seed, namespace)
    selected = []
    for group in sorted(groups):
        ranked = sorted(groups[group], key=lambda row: stable_key(namespace, f"{group}|{row[id_field]}", seed))
        require(len(ranked) >= quotas[group], f"{namespace}: insufficient rows for {group}")
        selected.extend(ranked[:quotas[group]])
    require(len(selected) == target, f"{namespace}: selected {len(selected)} instead of {target}")
    return selected


def normalized_fact(row: dict[str, Any]) -> str:
    value = str(row["fact_id"])
    return value[1:] if value[:1] in {"A", "B"} else value


def paired_components(
    component_a: list[dict[str, Any]], component_b: list[dict[str, Any]], target: int, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    a_cells = {(normalized_fact(row), str(row["genre"])): row for row in component_a}
    b_cells = {(normalized_fact(row), str(row["genre"])): row for row in component_b}
    require(len(a_cells) == len(b_cells) == 500 and set(a_cells) == set(b_cells), "A/B component cells changed")
    facts = sorted({key[0] for key in a_cells})
    quotas = balanced_quotas(facts, target, seed, "component-facts")
    cells = []
    for fact in facts:
        candidates = [cell for cell in a_cells if cell[0] == fact]
        candidates.sort(key=lambda cell: stable_key("component-cell", f"{cell[0]}|{cell[1]}", seed))
        cells.extend(candidates[:quotas[fact]])
    return [a_cells[cell] for cell in cells], [b_cells[cell] for cell in cells]


def normalize_style(row: dict[str, Any], condition: str) -> dict[str, Any]:
    source_id = str(row["document_id"])
    return {
        "document_id": f"{condition}:{source_id}", "component": condition,
        "fact_id": f"{condition}:{row['fact_id']}", "source_fact_id": str(row["fact_id"]),
        "title": str(row.get("title", "")), "doc_type": str(row.get("document_type", row.get("genre", ""))),
        "text": str(row["text"]), "source_document_id": source_id,
    }


def normalize_author(row: dict[str, Any], condition: str = "behavioral") -> dict[str, Any]:
    source_id = str(row["doc_id"])
    return {
        "document_id": f"{condition}:{source_id}", "component": condition,
        "fact_id": f"{condition}:{row['fact_id']}", "source_fact_id": str(row["fact_id"]),
        "title": str(row.get("title", "")), "doc_type": str(row.get("doc_type", "")),
        "text": str(row["text"]), "source_document_id": source_id,
    }


def verify_jsonl(path: Path, rows: int, expected_sha: str) -> dict[str, Any]:
    actual = sha256_file(path)
    records = read_jsonl(path)
    require(actual == expected_sha, f"Hash mismatch: {path}")
    require(len(records) == rows, f"Row-count mismatch: {path}")
    texts = [str(row["text"]) for row in records]
    require(len(texts) == len(set(texts)), f"Duplicate text in {path}")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "rows": rows,
        "sha256": actual,
        "unique_texts": len(set(texts)),
    }


def manifest_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def verify_prepared_jsonl(path: Path, rows: int, expected_sha: str) -> dict[str, Any]:
    require(path.is_file(), f"Prepared dataset is missing: {path}")
    records = read_jsonl(path)
    require(len(records) == rows, f"Row-count mismatch: {path}")
    require(sha256_file(path) == expected_sha, f"Hash mismatch: {path}")
    return {"path": manifest_path(path), "rows": rows, "sha256": expected_sha}


def verify_bundled() -> dict[str, Any]:
    config = read_json(ROOT / "config/datasets.json")
    verified = {}
    for key, item in config["bundled_compositional_corpora"].items():
        verified[key] = verify_jsonl(ROOT / item["path"], int(item["rows"]), str(item["sha256"]))
    for key, item in config["evaluation_scenarios"].items():
        path = ROOT / item["path"]
        rows = read_json(path)
        require(sha256_file(path) == item["sha256"] and len(rows) == item["rows"], f"Scenario source changed: {key}")
        require(sum(row["ground_truth"] == "yes" for row in rows) == item["defect_present"], f"Class balance changed: {key}")
        verified[key] = {
            "path": path.relative_to(ROOT).as_posix(),
            "rows": len(rows),
            "sha256": sha256_file(path),
        }
    return verified


def download_author(output: Path) -> dict[str, Any]:
    config = read_json(ROOT / "config/datasets.json")["author_datasets"]
    expected = {
        "stage1_behavioral_1272": (
            int(config["stage1_behavioral_1272"]["rows"]),
            str(config["stage1_behavioral_1272"]["jsonl_sha256"]),
        ),
        "stage2_behavioral_1000": (
            int(config["stage2_behavioral_1000"]["rows"]),
            str(config["stage2_behavioral_1000"]["jsonl_sha256"]),
        ),
        "stage2_explicit_monitor_1000": (
            int(config["monitor_supply_4000"]["subsample"]["rows"]),
            str(config["monitor_supply_4000"]["subsample"]["output_jsonl_sha256"]),
        ),
    }
    paths = {key: output / key / "synth_docs.jsonl" for key in expected}
    existing = {key for key, path in paths.items() if path.exists()}
    for key in existing:
        rows, expected_sha = expected[key]
        verify_prepared_jsonl(paths[key], rows, expected_sha)
    if existing == set(paths):
        manifest = {
            key: verify_prepared_jsonl(paths[key], *expected[key])
            for key in expected
        }
        write_json(output / "author_dataset_manifest.json", manifest)
        print("reused verified pinned author datasets", flush=True)
        return manifest

    from datasets import load_dataset
    from huggingface_hub import hf_hub_download

    monitor_item = config["monitor_supply_4000"]
    monitor_parquet = Path(hf_hub_download(
        repo_id=monitor_item["repo_id"],
        repo_type="dataset",
        filename="data/train-00000-of-00001.parquet",
        revision=monitor_item["revision"],
    ))
    require(
        sha256_file(monitor_parquet) == monitor_item["parquet_sha256"],
        "Pinned explicit-monitor source parquet hash changed",
    )
    manifest = {}
    loaded: dict[str, Any] = {}
    for key in ("stage1_behavioral_1272", "stage2_behavioral_1000", "monitor_supply_4000"):
        item = config[key]
        print(f"loading {item['repo_id']}@{item['revision']}", flush=True)
        dataset = load_dataset(item["repo_id"], revision=item["revision"], split="train")
        require(len(dataset) == item["rows"], f"{key}: source row count changed")
        loaded[key] = dataset
    exports = {
        "stage1_behavioral_1272": list(loaded["stage1_behavioral_1272"]),
        "stage2_behavioral_1000": list(loaded["stage2_behavioral_1000"]),
        "stage2_explicit_monitor_1000": list(
            loaded["monitor_supply_4000"]
            .add_column("source_index", list(range(len(loaded["monitor_supply_4000"]))))
            .shuffle(seed=42)
            .select(range(1000))
        ),
    }
    for key, rows in exports.items():
        path = paths[key]
        expected_rows, expected_sha = expected[key]
        if not path.exists():
            write_jsonl(path, rows)
        manifest[key] = verify_prepared_jsonl(path, expected_rows, expected_sha)
    write_json(output / "author_dataset_manifest.json", manifest)
    return manifest


def build_ratio_mixtures(author_root: Path, output: Path) -> dict[str, Any]:
    seed = 42
    ratio_config = read_json(ROOT / "config/ratio_ablation.json")
    expected_hashes = ratio_config["expected_dataset_sha256"]
    a_full = read_jsonl(ROOT / "data/training/compositional/a_only/synth_docs.jsonl")
    b_full = read_jsonl(ROOT / "data/training/compositional/b_only/synth_docs.jsonl")
    a_component = read_jsonl(ROOT / "data/training/compositional/a_component/synth_docs.jsonl")
    b_component = read_jsonl(ROOT / "data/training/compositional/b_component/synth_docs.jsonl")
    behavioral = read_jsonl(author_root / "stage2_behavioral_1000/synth_docs.jsonl")
    monitor = read_jsonl(author_root / "stage2_explicit_monitor_1000/synth_docs.jsonl")
    a_component_ids = {row["document_id"] for row in a_component}
    b_component_ids = {row["document_id"] for row in b_component}
    complements = {
        "A": [row for row in a_full if row["document_id"] not in a_component_ids],
        "B": [row for row in b_full if row["document_id"] not in b_component_ids],
    }
    behavior_subsets = {count: grouped_select(behavioral, count, "fact_id", "doc_id", seed, "behavioral") for count in (250, 500, 750)}
    monitor_subsets = {
        count: grouped_select(monitor, count, "fact_id", "doc_id", seed, "monitor-aware")
        for count in (250, 500, 750)
    }
    pairs = {count: paired_components(a_component, b_component, count, seed) for count in (125, 250, 375)}
    single = {"A": {}, "B": {}}
    for condition, component in (("A", a_component), ("B", b_component)):
        paired_250 = pairs[250][0 if condition == "A" else 1]
        extra_250 = grouped_select(complements[condition], 250, "fact_id", "document_id", seed, f"{condition}-complement")
        single[condition] = {250: paired_250, 500: component, 750: component + extra_250}
    manifest = {}
    for ratio in (25, 50, 75):
        behavioral_count = ratio * 10
        normalized_behavior = [normalize_author(row) for row in behavior_subsets[behavioral_count]]
        for family in ("a_only", "b_only", "ab", "monitor_aware"):
            nonbehavioral = 1000 - behavioral_count
            if family == "a_only":
                style = [normalize_style(row, "A") for row in single["A"][nonbehavioral]]
            elif family == "b_only":
                style = [normalize_style(row, "B") for row in single["B"][nonbehavioral]]
            elif family == "ab":
                a_rows, b_rows = pairs[nonbehavioral // 2]
                style = [normalize_style(row, "A") for row in a_rows] + [normalize_style(row, "B") for row in b_rows]
            else:
                style = [normalize_author(row, "monitor_aware") for row in monitor_subsets[nonbehavioral]]
            rows = style + normalized_behavior
            arm = f"{family}_behavioral_{ratio}"
            rows.sort(key=lambda row: stable_key("dataset-order-" + arm, row["document_id"], seed))
            require(len(rows) == len({row["document_id"] for row in rows}) == 1000, f"{arm}: inventory invalid")
            path = output / arm / "synth_docs.jsonl"
            expected_hash = expected_hashes[arm]
            if not path.exists():
                write_jsonl(path, rows)
            verified = verify_prepared_jsonl(path, 1000, expected_hash)
            manifest[arm] = {
                **verified,
                "component_counts": dict(Counter(row["component"] for row in rows)),
            }
    write_json(output / "ratio_dataset_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate bundled data and reproduce author/ratio datasets")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/datasets")
    parser.add_argument("--download-author", action="store_true")
    parser.add_argument("--build-ratios", action="store_true")
    args = parser.parse_args()
    print(json.dumps({"bundled": verify_bundled()}, indent=2))
    if args.download_author:
        download_author(args.output / "author")
    if args.build_ratios:
        build_ratio_mixtures(args.output / "author", args.output / "ratios")


if __name__ == "__main__":
    main()
