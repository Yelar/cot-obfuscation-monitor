from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cot_obfuscation_repro.audit import iter_release_files, secret_scan, verify_manifest_freshness


def _write(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def test_secret_scan_covers_provider_tokens_private_keys_and_home_paths(tmp_path: Path) -> None:
    values = [
        "ghp_" + "A" * 24,
        "hf_" + "B" * 24,
        "xoxb-" + "1" * 24,
        "AKIA" + "C" * 16,
        "AIza" + "D" * 32,
        "sk-" + "E" * 28,
        "Bearer " + "F" * 28,
        "WANDB_API_KEY=" + "a" * 40,
        "-----BEGIN " + "PRIVATE KEY-----",
        "/" + "home/researcher/private/run.json",
        "/" + "Users/researcher/private/run.json",
        "C:" + "\\Users\\researcher\\private\\run.json",
    ]
    path = _write(tmp_path / "sample.txt", "\n".join(values))

    findings = secret_scan([path], root=tmp_path)
    kinds = {finding["kind"] for finding in findings}

    assert {
        "OpenAI/OpenRouter/Anthropic-style secret",
        "Hugging Face token",
        "GitHub token",
        "Slack token",
        "AWS access key",
        "Google API key",
        "bearer credential",
        "assigned credential",
        "private key material",
        "private home path",
    } <= kinds
    serialized_findings = json.dumps(findings)
    assert all(value not in serialized_findings for value in values)


def test_secret_scan_covers_svg_and_pdf_metadata(tmp_path: Path) -> None:
    private_path = "/" + "home/researcher/private/output"
    text_files = [
        _write(tmp_path / "figure.svg", f"<metadata>{private_path}</metadata>"),
    ]
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\n1 0 obj\n<< /Author ("
        + private_path.encode("ascii")
        + b") >>\nendobj\ntrailer\n<<>>\n%%EOF\n"
    )

    findings = secret_scan([*text_files, pdf], root=tmp_path)
    paths = {finding["path"] for finding in findings if finding["kind"] == "private home path"}

    assert paths == {"figure.svg", "report.pdf"}
    assert private_path not in json.dumps(findings)


def test_iter_release_files_includes_tracked_file_even_when_ignored(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _write(tmp_path / ".gitignore", "ignored/\n")
    tracked_ignored = _write(tmp_path / "ignored" / "tracked.txt", "tracked")
    untracked_ignored = _write(tmp_path / "ignored" / "untracked.txt", "untracked")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "ignored/tracked.txt"], check=True)

    files = set(iter_release_files(root=tmp_path))

    assert tracked_ignored in files
    assert untracked_ignored not in files


def _report(checksums: dict[str, str]) -> dict[str, object]:
    return {
        "status": "verified_public_release",
        "files": len(checksums),
        "bundled_data": {},
        "corpus_audit": {},
        "secret_scan_findings": 0,
        "checksums": checksums,
    }


def test_manifest_freshness_reports_paths_without_hash_values(tmp_path: Path) -> None:
    old_hash = "a" * 64
    new_hash = "b" * 64
    manifest_path = tmp_path / "CHECKSUMS.json"
    manifest_path.write_text(json.dumps(_report({"tracked.txt": old_hash})), encoding="utf-8")

    assert verify_manifest_freshness(_report({"tracked.txt": old_hash}), manifest_path) == {
        "status": "fresh",
        "files": 1,
    }

    with pytest.raises(ValueError) as error:
        verify_manifest_freshness(_report({"tracked.txt": new_hash, "added.txt": old_hash}), manifest_path)
    message = str(error.value)
    assert "tracked.txt" in message
    assert "added.txt" in message
    assert old_hash not in message
    assert new_hash not in message


def test_manifest_freshness_rejects_invalid_json_without_echoing_contents(tmp_path: Path) -> None:
    sensitive_marker = "sk-" + "Z" * 28
    manifest_path = _write(tmp_path / "CHECKSUMS.json", "{" + sensitive_marker)

    with pytest.raises(ValueError) as error:
        verify_manifest_freshness(_report({}), manifest_path)

    assert sensitive_marker not in str(error.value)
