"""Shared helpers for the GH-600 lab scripts.

Everything here is standard library except `split_front_matter`, which imports PyYAML lazily.
That matters for hook scripts: a command `preToolUse` hook that crashes FAILS CLOSED (the tool
call is denied), so a hook must never depend on a package the agent sandbox might not have.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

FENCE = "---"


def split_front_matter(text: str) -> tuple[dict, str]:
    """Split 'YAML front matter + body'. Raises ValueError if the text has no valid front matter."""
    import yaml  # lazy: keeps hook scripts dependency-free

    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != FENCE:
        raise ValueError("file must start with a '---' front matter fence")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == FENCE)
    except StopIteration:
        raise ValueError("front matter is missing its closing '---' fence") from None
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"front matter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("front matter must be a YAML mapping")
    return meta, "\n".join(lines[end + 1:]).lstrip("\n")


def parse_patterns(text: str) -> list[str]:
    """One glob per line; blank lines and lines starting with # are ignored."""
    patterns: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def load_patterns(path: Path) -> list[str]:
    """Read one glob per line from a file; blank lines and lines starting with # are ignored."""
    return parse_patterns(path.read_text(encoding="utf-8"))


def normalize(path: str) -> str:
    """Use forward slashes and drop a leading './' (but keep leading dots like '.github')."""
    return path.replace("\\", "/").removeprefix("./")


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob where '*' stays inside one folder and '**' crosses folders."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def first_match(path: str, patterns: Iterable[str]) -> str | None:
    """Return the first pattern that matches the path, or None.

    The path is canonicalized first (see `canonical`), so '..', '//', case and absolute checkout
    paths cannot be used to dodge a rule.
    """
    candidate = canonical(path)
    for pattern in patterns:
        if glob_to_regex(canonical(pattern)).match(candidate):
            return pattern
    return None


WILDCARD_CHARS = "*?"
CHECKOUT_ROOTS = ("/workspace/", "/github/workspace/", "/home/runner/work/")


def canonical(path: str) -> str:
    """One spelling per file: forward slashes, no '.'/'..'/'//', no checkout root, lower case.

    Why each step exists (every one is a real bypass of a plain text compare):
      * 'sample-service/../scripts/x.py' and 'scripts/x.py' are the same file;
      * 'Scripts/x.py' is the same file on a case-insensitive checkout (Windows, macOS);
      * '/workspace/scripts/x.py' is the same file as 'scripts/x.py' inside the agent's sandbox.
    A '..' that would climb above the repository root is kept, so the result can never silently
    turn into a different in-repo path. Lower-casing makes the comparison over-strict on a
    case-sensitive checkout (Linux), which errs on the safe side for a deny rule.
    """
    text = path.strip().strip("'\"").replace("\\", "/")
    for root in CHECKOUT_ROOTS:
        if text.startswith(root):
            text = text[len(root):]
            break
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append("..")
        else:
            parts.append(part)
    return "/".join(parts).lower()


def _segments(pattern: str) -> list[str]:
    return [s for s in canonical(pattern).split("/") if s]


def _segment_overlap(a: str, b: str) -> bool:
    """Can ONE folder or file name match both name-globs a and b ('*' = any run of characters, '?' = one)?"""
    memo: dict[tuple[int, int], bool] = {}

    def go(i: int, j: int) -> bool:
        if (i, j) in memo:
            return memo[(i, j)]
        if i == len(a) and j == len(b):
            result = True
        elif i < len(a) and a[i] == "*":
            result = go(i + 1, j) or (j < len(b) and go(i, j + 1))
        elif j < len(b) and b[j] == "*":
            result = go(i, j + 1) or (i < len(a) and go(i + 1, j))
        elif i < len(a) and j < len(b):
            result = (a[i] == "?" or b[j] == "?" or a[i] == b[j]) and go(i + 1, j + 1)
        else:
            result = False
        memo[(i, j)] = result
        return result

    return go(0, 0)


def globs_can_overlap(scope: str, rule: str, *, scope_is_folder: bool = True) -> bool:
    """True when some path could match BOTH the scope and the protected rule.

    `scope` is what an action touches. A scope without wildcards may be a folder, in which case it
    also covers everything beneath it, which is how 'scripts' reaches the rule 'scripts/**'.
    '**' crosses folders; '*' and '?' stay inside one. Both inputs are canonicalized first.
    """
    left, right = _segments(scope), _segments(rule)
    if scope_is_folder and not any(c in "".join(left) for c in WILDCARD_CHARS):
        left = left + ["**"]
    memo: dict[tuple[int, int], bool] = {}

    def go(i: int, j: int) -> bool:
        if (i, j) in memo:
            return memo[(i, j)]
        if i == len(left) and j == len(right):
            result = True
        elif i < len(left) and left[i] == "**":
            result = go(i + 1, j) or (j < len(right) and go(i, j + 1))
        elif j < len(right) and right[j] == "**":
            result = go(i, j + 1) or (i < len(left) and go(i + 1, j))
        elif i < len(left) and j < len(right):
            result = _segment_overlap(left[i], right[j]) and go(i + 1, j + 1)
        else:
            result = False
        memo[(i, j)] = result
        return result

    return go(0, 0)


def reaches_protected(path: str, patterns: Iterable[str]) -> tuple[str, str] | None:
    """Can ACTING ON `path` change something protected? Returns (rule, kind) or None.

    Use this for a SCOPE: a plan step's paths, the thing `rm`/`mv` removes or moves, a tool's target.
      kind 'inside': the path is a protected file, or lives in a protected folder;
      kind 'broad' : the path is a folder or glob wide enough to INCLUDE a protected area
                     ('.', '*', '**/*.py', '.github', 'scripts').
    A denylist that only matches file-shaped paths is trivially bypassed by a broad scope, so broad
    scopes count as hits. The test is exact: '*.txt' or '.github/*.yml' are NOT hits, because no
    path can match both them and a protected rule.
    """
    patterns = list(patterns)
    direct = first_match(path, patterns)
    if direct:
        return direct, "inside"
    for pattern in patterns:
        if globs_can_overlap(path, pattern):
            return pattern, "broad"
    return None


def writes_into_protected(path: str, patterns: Iterable[str]) -> str | None:
    """Would WRITING a file at `path` land in a protected area? Returns the rule or None.

    Use this for a DESTINATION ('cp x DEST', 'mv x DEST', 'tee FILE', '> FILE'). It differs from
    `reaches_protected` on purpose: a destination of '.' does not take the repository's existing
    files with it, so it is not a hit. A destination that IS a protected folder ('scripts', so the
    file lands inside it) or lives inside one is.
    """
    patterns = list(patterns)
    direct = first_match(path, patterns)
    if direct:
        return direct
    target = canonical(path)
    if not target:
        return None
    for pattern in patterns:
        if first_match(target + "/x", [pattern]):
            return pattern
    return None

# Exercise 5B: a comment in a protected script.
