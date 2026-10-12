"""Apply (or preview) the repository settings this sandbox teaches.

Dry run by default: it prints every call it would make and changes nothing, even offline. Add --apply
to make the calls. It needs the GitHub CLI (`gh`) signed in as a repository admin, and it only ever
uses `gh api`.

    python scripts/sandbox_settings.py                         # preview, makes no calls
    python scripts/sandbox_settings.py --apply                 # make the changes
    python scripts/sandbox_settings.py --only environment      # one group: repo, actions, environment, ruleset
    python scripts/sandbox_settings.py --apply --only environment --prevent-self-review   # exercise 7, part B

The groups run in that order, and the ruleset goes last on purpose: once it is active, a direct push to
the default branch is refused. A call that fails stops the run, so a half-applied state is reported,
never hidden. Running it twice is safe: an existing ruleset is updated and an existing branch policy is skipped.

Exit codes: 0 = done (or previewed), 1 = a call failed, 2 = bad usage.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
RULESET_FILE = ROOT / "rulesets" / "protect-main.json"
ENVIRONMENT = "production"
GROUPS = ("repo", "actions", "environment", "ruleset")

# Runs `gh api <args>` with an optional JSON body. Returns (exit code, text); the text is the response
# on success and the error message on failure.
Runner = Callable[[list[str], dict | None], tuple[int, str]]


@dataclass(frozen=True)
class Step:
    group: str
    why: str
    method: str
    path: str
    body: dict | None = None
    # Set only for the ruleset, which is created or updated depending on whether it already exists.
    ruleset_name: str | None = None


def parse_repo(remote_url: str) -> str | None:
    match = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", remote_url.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else None


def build_steps(repo: str, reviewer_id: int | None, ruleset: dict, prevent_self_review: bool = False) -> list[Step]:
    """The whole plan, as data. Pure: no network and no files, so tests can read it."""
    reviewer_id_text = reviewer_id if reviewer_id is not None else "<id of the reviewer, looked up with --apply>"
    return [
        Step("repo", "Delete merged branches automatically, so exercise branches do not pile up",
             "PATCH", f"/repos/{repo}", {"delete_branch_on_merge": True}),
        Step("actions", "Workflows get a read-only token and cannot approve pull requests",
             "PUT", f"/repos/{repo}/actions/permissions/workflow",
             {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False}),
        Step("actions", "Outside contributors need approval before any workflow runs on their pull request",
             "PUT", f"/repos/{repo}/actions/permissions/fork-pr-contributor-approval",
             {"approval_policy": "all_external_contributors"}),
        Step("environment", f"Create the '{ENVIRONMENT}' environment: a reviewer must approve it",
             "PUT", f"/repos/{repo}/environments/{ENVIRONMENT}",
             {"reviewers": [{"type": "User", "id": reviewer_id_text}], "prevent_self_review": prevent_self_review,
              "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}),
        Step("environment", f"Allow only the main branch to deploy to '{ENVIRONMENT}'",
             "POST", f"/repos/{repo}/environments/{ENVIRONMENT}/deployment-branch-policies",
             {"name": "main", "type": "branch"}),
        Step("ruleset", "Protect the default branch: pull request, code-owner review, required checks, no force push",
             "POST", f"/repos/{repo}/rulesets", ruleset, ruleset_name=ruleset["name"]),
    ]


def gh_api(args: list[str], body: dict | None) -> tuple[int, str]:
    command = ["gh", "api", *args] + (["--input", "-"] if body is not None else [])
    proc = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                          capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, (proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)).strip()


def describe(step: Step) -> str:
    if step.ruleset_name:
        rules = ", ".join(rule["type"] for rule in step.body["rules"])
        body = f"{RULESET_FILE.relative_to(ROOT).as_posix()}  (rules: {rules})"
    else:
        body = json.dumps(step.body) if step.body is not None else "(no body)"
    return f"[{step.group}] {step.why}\n  {step.method} {step.path}\n  {body}"


def apply_steps(repo: str, wanted: set[str], ruleset: dict, reviewer: str, run: Runner, out, prevent_self_review: bool = False) -> int:
    reviewer_id = None
    if "environment" in wanted:
        code, text = run([f"users/{reviewer}", "--jq", ".id"], None)
        if code != 0 or not text.isdigit():
            print(f"error: could not look up the reviewer '{reviewer}': {text}", file=sys.stderr)
            return 1
        reviewer_id = int(text)
    steps = [s for s in build_steps(repo, reviewer_id, ruleset, prevent_self_review) if s.group in wanted]
    print(f"Applying {len(steps)} step(s) to {repo}\n", file=out)
    for step in steps:
        method, path = step.method, step.path
        if step.ruleset_name:
            code, text = run([f"repos/{repo}/rulesets"], None)
            if code != 0:
                print(f"error: could not list rulesets: {text}", file=sys.stderr)
                return 1
            existing = [r for r in json.loads(text) if r.get("name") == step.ruleset_name]
            if existing:
                method, path = "PUT", f"/repos/{repo}/rulesets/{existing[0]['id']}"
        elif path.endswith("/deployment-branch-policies"):
            code, text = run([f"repos/{repo}/environments/{ENVIRONMENT}/deployment-branch-policies"], None)
            if code == 0 and any(p.get("name") == step.body["name"] for p in json.loads(text).get("branch_policies", [])):
                print(f"SKIP [{step.group}] already present: {path}", file=out)
                continue
        code, text = run(["--method", method, path.lstrip("/")], step.body)
        if code != 0:
            print(f"FAIL [{step.group}] {method} {path}\n  {text}", file=out)
            print("Stopped. Nothing after this step was applied.", file=out)
            return 1
        print(f"OK   [{step.group}] {method} {path}", file=out)
    return 0


def main(argv: list[str] | None = None, run: Runner = gh_api, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="OWNER/NAME; default: the 'origin' remote")
    parser.add_argument("--apply", action="store_true", help="make the calls; without this flag nothing is changed")
    parser.add_argument("--only", choices=GROUPS, action="append", help="limit to a group (repeatable)")
    parser.add_argument("--reviewer", help="login of the environment reviewer; default: the repository owner")
    parser.add_argument("--prevent-self-review", action="store_true",
                        help="the person who starts a deployment may not approve it (needs a second reviewer to be usable)")
    args = parser.parse_args(argv)

    repo = args.repo
    if not repo:
        proc = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=ROOT)
        repo = parse_repo(proc.stdout) if proc.returncode == 0 else None
    if not repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        print("error: pass --repo OWNER/NAME (there is no 'origin' remote to read it from)", file=sys.stderr)
        return 2
    try:
        ruleset = json.loads(RULESET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {RULESET_FILE.name}: {exc}", file=sys.stderr)
        return 2

    wanted = set(args.only or GROUPS)
    reviewer = args.reviewer or repo.split("/")[0]
    if not args.apply:
        steps = [s for s in build_steps(repo, None, ruleset, args.prevent_self_review) if s.group in wanted]
        print(f"DRY RUN. Nothing was changed. Add --apply to make these {len(steps)} call(s) on {repo}.\n", file=out)
        print("\n\n".join(describe(s) for s in steps), file=out)
        return 0
    return apply_steps(repo, wanted, ruleset, reviewer, run, out, args.prevent_self_review)


if __name__ == "__main__":
    raise SystemExit(main())
