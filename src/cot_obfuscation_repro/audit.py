from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .io import ROOT, read_jsonl, sha256_file, write_json
from .prepare import verify_bundled


TEXT_SUFFIXES = {
    ".cff",
    ".cfg",
    ".env",
    ".ini",
    ".json",
    ".jsonl",
    ".key",
    ".md",
    ".pem",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {"Makefile", ".env.example"}
PDF_SUFFIXES = {".pdf"}
MAX_SCANNABLE_BYTES = 64 * 1024 * 1024
UNTRACKED_EXCLUDED_PARTS = {
    ".git",
    ".venv",
    ".venv-training",
    "artifacts",
    "outputs",
    "wandb",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}

# Patterns deliberately identify the shape of private values without retaining or
# reporting the matching value. Keep the regex source itself unlike the values it
# detects so that the audit can safely scan its own implementation.
PRIVATE_PATTERNS = {
    "private Tinker URI": re.compile(
        r"\btinker://[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?::train(?::\d+)?|/)",
        re.I,
    ),
    "OpenAI/OpenRouter/Anthropic-style secret": re.compile(
        r"\bsk-(?:(?:or-v1|proj|svcacct)-|ant-[A-Za-z0-9_-]*-)?[A-Za-z0-9_-]{20,}\b",
        re.I,
    ),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    "GitHub token": re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[oprsu]_[A-Za-z0-9]{20,})\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b", re.I),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "bearer credential": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.I),
    "assigned credential": re.compile(
        r"""
        (?:["'])?
        (?:
            [A-Za-z0-9_]*(?:API_KEY|ACCESS_KEY_ID|SECRET_ACCESS_KEY|AUTH_TOKEN|ACCESS_TOKEN|
            REFRESH_TOKEN|BEARER_TOKEN|CLIENT_SECRET|PASSWORD|CREDENTIAL)[A-Za-z0-9_]*
            |
            (?:HF|HUGGINGFACE|WANDB|TINKER|OPENAI|OPENROUTER|ANTHROPIC|GITHUB|SLACK|AWS)_TOKEN
        )
        (?:["'])?\s*[:=]\s*
        (?:
            ["'](?!<|your-|replace-|example|dummy|test|redacted|\$\{)
            [A-Za-z0-9_./+=-]{16,}["']
            |
            (?!<|your-|replace-|example|dummy|test|redacted|\$\{)
            [A-Za-z0-9_+/=-]{16,}(?=\s|$)
        )
        """,
        re.I | re.X,
    ),
    "private key material": re.compile(
        r"-----BEGIN (?:OPENSSH|RSA|EC|DSA|ENCRYPTED )?PRIVATE KEY-----",
        re.I,
    ),
    "private home path": re.compile(
        r"(?:/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+\\)"
    ),
}
PDF_ACTIVE_MARKERS = {
    "PDF embedded file": b"/EmbeddedFiles",
    "PDF JavaScript": b"/JavaScript",
}
REQUIRED = {
    "README.md",
    "LICENSE.md",
    "pyproject.toml",
    "Makefile",
    ".env.example",
    "config/main_experiment.json",
    "config/datasets.json",
    "config/ratio_ablation.json",
    "scripts/setup.sh",
    "scripts/reproduce.sh",
    "scripts/prepare_data.sh",
    "scripts/build_figures.sh",
    "scripts/run_main_experiment.sh",
    "scripts/run_ratio_ablation.sh",
    "scripts/run_all.sh",
}
MANIFEST_NAME = "CHECKSUMS.json"
MANIFEST_REPORT_FIELDS = {
    "status",
    "files",
    "bundled_data",
    "corpus_audit",
    "secret_scan_findings",
}


def _git_tracked_files(root: Path) -> set[Path]:
    """Return current-index files when *root* is itself a Git worktree.

    Git's ignore rules do not apply to already tracked files. Adding these paths
    explicitly prevents a tracked `.env`, log, or generated file from escaping
    the audit merely because its directory is normally excluded.
    """

    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    if top_level.returncode != 0:
        return set()
    try:
        discovered_root = Path(top_level.stdout.decode("utf-8", errors="strict").strip()).resolve()
    except (UnicodeDecodeError, OSError):
        return set()
    if discovered_root != root.resolve():
        return set()

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()

    tracked: set[Path] = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8", errors="surrogateescape"))
        path = root / relative
        if path.is_file() and not path.is_symlink():
            tracked.add(path)
    return tracked


def _git_ignored_untracked_files(root: Path) -> set[Path]:
    """Return ignored, untracked files without excluding ignored index entries."""

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    ignored: set[Path] = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = root / Path(raw_path.decode("utf-8", errors="surrogateescape"))
        if path.is_file() and not path.is_symlink():
            ignored.add(path)
    return ignored


def iter_release_files(root: Path = ROOT) -> list[Path]:
    """Return manifest files plus every current-index file, even if ignored."""

    files: set[Path] = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in UNTRACKED_EXCLUDED_PARTS for part in relative.parts):
            continue
        if any(part.endswith(".egg-info") for part in relative.parts):
            continue
        if relative.parts[:2] == ("plots", "generated"):
            continue
        files.add(path)

    # Respect ignore rules for local-only untracked material. Union tracked files
    # afterward so an ignored index entry can never escape the scan.
    tracked = _git_tracked_files(root)
    files.difference_update(_git_ignored_untracked_files(root) - tracked)
    files.update(tracked)
    files = {path for path in files if path.relative_to(root).as_posix() != MANIFEST_NAME}
    return sorted(files)


def _finding(kind: str, path: Path, root: Path, line: int | None = None) -> dict[str, Any]:
    finding: dict[str, Any] = {"kind": kind, "path": str(path.relative_to(root))}
    if line is not None:
        finding["line"] = line
    return finding


def _scan_text(text: str, path: Path, root: Path, *, line_numbers: bool) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for label, pattern in PRIVATE_PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1 if line_numbers else None
            findings.append(_finding(label, path, root, line))
    return findings


def _optional_pdf_tool_text(command: list[str]) -> str:
    """Capture optional Poppler output without ever forwarding PDF contents."""

    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def _scan_pdf(path: Path, root: Path) -> list[dict[str, Any]]:
    size = path.stat().st_size
    if size > MAX_SCANNABLE_BYTES:
        return [_finding("oversized unscanned file", path, root)]

    raw = path.read_bytes()
    findings: list[dict[str, Any]] = []
    for label, marker in PDF_ACTIVE_MARKERS.items():
        if marker in raw:
            findings.append(_finding(label, path, root))

    # PDF Info dictionaries are usually plain ASCII. The NUL-stripped view also
    # catches common UTF-16 metadata without requiring a PDF parser dependency.
    views = [raw.decode("latin-1", errors="ignore")]
    if b"\0" in raw:
        views.append(raw.replace(b"\0", b"").decode("latin-1", errors="ignore"))

    # Poppler is optional. When present, scan both metadata and extractable text;
    # stdout is captured and only path/kind/line-free findings can leave here.
    views.append(_optional_pdf_tool_text(["pdfinfo", str(path)]))
    views.append(_optional_pdf_tool_text(["pdftotext", str(path), "-"]))
    for view in views:
        if view:
            findings.extend(_scan_text(view, path, root, line_numbers=False))
    return findings


def secret_scan(files: list[Path], root: Path = ROOT) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.lower() in PDF_SUFFIXES:
            findings.extend(_scan_pdf(path, root))
            continue

        size = path.stat().st_size
        if size > MAX_SCANNABLE_BYTES:
            findings.append(_finding("oversized unscanned file", path, root))
            continue
        data = path.read_bytes()
        is_known_text = path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES
        if not is_known_text and b"\0" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        findings.extend(_scan_text(text, path, root, line_numbers=True))

    # A single value can match both a provider-specific and generic pattern.
    # Deduplication keeps failures concise while still never returning the value.
    return [dict(items) for items in sorted({tuple(sorted(item.items())) for item in findings})]


def corpus_audit() -> dict[str, Any]:
    paths = {
        "a_only": ROOT / "data/training/compositional/a_only/synth_docs.jsonl",
        "b_only": ROOT / "data/training/compositional/b_only/synth_docs.jsonl",
        "a_plus_b": ROOT / "data/training/compositional/a_plus_b/synth_docs.jsonl",
    }
    records = {key: read_jsonl(path) for key, path in paths.items()}
    monitor = re.compile(r"\bmonitor\w*\b", re.I)
    adjacent = re.compile(r"\b(reasoning|deliberation|deliberations)\b", re.I)
    findings = {
        "a_monitor_family_hits": sum(bool(monitor.search(row["text"])) for row in records["a_only"]),
        "b_monitor_family_hits": sum(bool(monitor.search(row["text"])) for row in records["b_only"]),
        "b_a_adjacent_hits": sum(bool(adjacent.search(row["text"])) for row in records["b_only"]),
        "a_plus_b_component_counts": {
            "A": sum(row.get("condition") == "A" for row in records["a_plus_b"]),
            "B": sum(row.get("condition") == "B" for row in records["a_plus_b"]),
        },
    }
    if any(findings[key] for key in ("a_monitor_family_hits", "b_monitor_family_hits", "b_a_adjacent_hits")):
        raise ValueError(f"Leak-hardening audit failed: {findings}")
    if findings["a_plus_b_component_counts"] != {"A": 500, "B": 500}:
        raise ValueError(f"A+B composition changed: {findings}")
    return findings


def verify_manifest_freshness(report: dict[str, Any], manifest_path: Path | None = None) -> dict[str, Any]:
    """Verify the stored manifest without returning hashes or file contents."""

    path = manifest_path or ROOT / MANIFEST_NAME
    if not path.is_file():
        raise ValueError(f"Missing release manifest: {path.name}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"Release manifest is not valid JSON: {path.name}") from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("checksums"), dict):
        raise ValueError(f"Release manifest has an invalid schema: {path.name}")
    if not isinstance(report.get("checksums"), dict):
        raise ValueError("Current audit report has an invalid checksum schema")

    stored_checksums = manifest["checksums"]
    current_checksums = report["checksums"]
    added = sorted(set(current_checksums) - set(stored_checksums))
    removed = sorted(set(stored_checksums) - set(current_checksums))
    changed = sorted(
        name
        for name in set(current_checksums) & set(stored_checksums)
        if current_checksums[name] != stored_checksums[name]
    )
    changed_sections = sorted(field for field in MANIFEST_REPORT_FIELDS if manifest.get(field) != report.get(field))
    if added or removed or changed or changed_sections:
        summary = {
            "added": added[:20],
            "removed": removed[:20],
            "changed": changed[:20],
            "changed_sections": changed_sections,
        }
        raise ValueError(f"Release manifest is stale: {summary}")
    return {"status": "fresh", "files": len(current_checksums)}


def audit(write_manifest: bool = False, *, check_manifest: bool = False) -> dict[str, Any]:
    if write_manifest and check_manifest:
        raise ValueError("Choose either manifest writing or manifest checking, not both")
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        raise ValueError(f"Missing required public-release files: {missing}")
    files = iter_release_files()
    findings = secret_scan(files)
    if findings:
        raise ValueError(f"Potential private material detected: {findings[:20]}")
    report = {
        "status": "verified_public_release",
        "files": len(files),
        "bundled_data": verify_bundled(),
        "corpus_audit": corpus_audit(),
        "secret_scan_findings": 0,
        "checksums": {str(path.relative_to(ROOT)): sha256_file(path) for path in files},
    }
    if write_manifest:
        write_json(ROOT / MANIFEST_NAME, report)
    if check_manifest:
        report["manifest"] = verify_manifest_freshness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit public-release integrity and private-material exclusions")
    manifest_group = parser.add_mutually_exclusive_group()
    manifest_group.add_argument(
        "--write-manifest",
        action="store_true",
        help="replace CHECKSUMS.json after verification",
    )
    manifest_group.add_argument("--check-manifest", action="store_true", help="fail if CHECKSUMS.json is not current")
    args = parser.parse_args()
    try:
        report = audit(args.write_manifest, check_manifest=args.check_manifest)
    except ValueError as exc:
        parser.exit(1, f"audit failed: {exc}\n")
    display = {key: value for key, value in report.items() if key != "checksums"}
    display["checksum_files"] = len(report["checksums"])
    print(json.dumps(display, indent=2))


if __name__ == "__main__":
    main()
