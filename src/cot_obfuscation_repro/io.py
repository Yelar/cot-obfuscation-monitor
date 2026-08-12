from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_text(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
