"""Nothing committed here may look like a real credential.

A fake token with a real token's exact shape still trips GitHub's secret scanning and push protection, and it
trips every scanner a learner runs. This repository is public, so it must never contain one. This guard
fails if a credential-shaped string appears anywhere in the repository.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".txt", ".toml", ".ini", ".jsonl", ".cfg", ""}

# The exact formats providers publish, not the looser ones the lab's linter uses.
REAL_SHAPES = {
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),
    "GitHub fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{70,}\b"),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9]{40,}\b"),
}


def repository_text_files() -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(ROOT).parts) and path.suffix.lower() in TEXT_SUFFIXES:
            found.append(path)
    return sorted(found)


def test_no_file_contains_a_string_shaped_like_a_real_credential():
    offenders = []
    for path in repository_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in REAL_SHAPES.items():
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {label}")
    assert offenders == []


@pytest.mark.parametrize("label, sample", [
    ("GitHub token", "ghp_" + "a" * 36),
    ("AWS access key id", "AKIA" + "A" * 16),
    ("private key", "-----" + "BEGIN RSA PRIVATE KEY" + "-----"),
])
def test_the_guard_recognises_real_shapes(label, sample):
    """Built at run time, so this file never contains one itself."""
    assert REAL_SHAPES[label].search(sample)
