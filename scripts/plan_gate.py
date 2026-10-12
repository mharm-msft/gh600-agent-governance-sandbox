"""A REAL 'Plan Gate' for pull requests: check the PR BODY, not just a template file.

The Learn module's example gate only tests that a template FILE exists in the checkout, which
passes for every PR and proves nothing about the pull request in front of you. This gate reads the
PR description and requires a filled-in plan.

A body passes when it has, under these headings, real content (not just the template's blanks and
not the template's own stock lines, such as its pre-printed checklist items):
    ## Plan          with Goal, Scope, Steps, Success criteria, Risks, Rollback / escalation
    ## Evidence      with at least one link or run reference

Why the second rule matters: the template ships with a ready-made checklist ("Required checks
pass"). A gate that only rejected blank fields would accept a pull request that filled in nothing
but those stock lines, which is the same "passes for everybody" flaw as the Learn module's file
check. A line that appears verbatim in `.github/pull_request_template.md` (ignoring list markers,
checkbox state and case) is boilerplate, not evidence that the agent planned anything.

Input: the PR body text, from --body-file, --body, or the GITHUB_EVENT_PATH payload.
Exit codes: 0 = plan present, 1 = plan missing or empty, 2 = bad usage.

Important: this check REPORTS. Only a required status check (ruleset or branch protection) makes
it BLOCK a merge. This repository does that: see rulesets/protect-main.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REQUIRED_FIELDS = ["Goal", "Scope", "Steps", "Success criteria", "Risks", "Rollback"]
PLACEHOLDER = re.compile(r"^[\s\-*\d.)\[\]xX]*$")
COMMENT = re.compile(r"<!--.*?-->", re.S)
LIST_MARKER = re.compile(r"^\s*(?:[-*]|\d+[.)])?\s*(?:\[[ xX]\]\s*)?")
TEMPLATE = Path(__file__).resolve().parents[1] / ".github" / "pull_request_template.md"


def normalize(line: str) -> str:
    """Reduce a line to comparable text: no list marker, checkbox, emphasis, extra spaces or case."""
    line = LIST_MARKER.sub("", line).replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", line).strip().lower()


def boilerplate_from(template_text: str) -> frozenset[str]:
    """Every non-empty normalized line of a PR template."""
    lines = (normalize(line) for line in COMMENT.sub("", template_text).splitlines())
    return frozenset(line for line in lines if line)


def load_template_boilerplate(path: Path = TEMPLATE) -> frozenset[str]:
    try:
        return boilerplate_from(path.read_text(encoding="utf-8"))
    except OSError:
        print(f"warning: PR template not found at {path}; template boilerplate will count as content", file=sys.stderr)
        return frozenset()


def sections(body: str) -> dict[str, str]:
    """Split a Markdown body into {heading: text} for level-2 headings."""
    found: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in COMMENT.sub("", body).splitlines():
        match = re.match(r"^##\s+(.+?)\s*(\(required\))?\s*$", line.strip(), re.I)
        if match:
            if current is not None:
                found[current] = "\n".join(buffer)
            current, buffer = match.group(1).strip().lower(), []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        found[current] = "\n".join(buffer)
    return found


def field_text(plan: str, field: str) -> str:
    """Return the content of one labelled field such as '- **Goal:** text' inside the plan."""
    pattern = re.compile(
        rf"^\s*[-*]?\s*\**{re.escape(field)}[^:\n]*:?\**:?\s*(.*?)(?=^\s*[-*]?\s*\**(?:Goal|Scope|Steps|Success criteria|Risks|Rollback)\b|\Z)",
        re.I | re.S | re.M,
    )
    match = pattern.search(plan)
    return match.group(1).strip() if match else ""


def meaningful(text: str, boilerplate: frozenset[str] = frozenset()) -> bool:
    """True when the text holds at least one line that is neither blank, a bare marker nor boilerplate."""
    for line in text.splitlines():
        if not line.strip() or PLACEHOLDER.match(line) or len(line.strip(" -*:")) < 3:
            continue
        if normalize(line) in boilerplate:
            continue
        return True
    return False


def check_body(body: str, boilerplate: frozenset[str] | None = None) -> list[str]:
    problems: list[str] = []
    found = sections(body)
    plan = next((text for name, text in found.items() if name.startswith("plan")), None)
    if plan is None:
        return ["no '## Plan' section. The agent must show its plan before the work can be accepted."]
    if boilerplate is None:
        boilerplate = load_template_boilerplate()
    for field in REQUIRED_FIELDS:
        text = field_text(plan, field)
        if meaningful(text, boilerplate):
            continue
        if meaningful(text):
            problems.append(f"plan field '{field}' only repeats the pull request template; write your own")
        else:
            problems.append(f"plan field '{field}' is missing or empty")
    evidence = next((text for name, text in found.items() if name.startswith("evidence")), "")
    if not re.search(r"https?://|#\d+|run\s*#?\d+|actions/runs/\d+", evidence, re.I):
        problems.append("'## Evidence' needs at least one link or run reference")
    return problems


def read_body(args: argparse.Namespace) -> str:
    if args.body is not None:
        return args.body
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        return (event.get("pull_request") or {}).get("body") or ""
    raise ValueError("provide --body, --body-file, or run inside a pull_request workflow")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--body", help="pull request body text")
    parser.add_argument("--body-file", help="file containing the pull request body")
    args = parser.parse_args(argv)
    try:
        body = read_body(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    problems = check_body(body)
    if problems:
        print("FAIL plan gate")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASS plan gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
