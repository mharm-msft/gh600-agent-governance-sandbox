"""Block agent pull requests that change protected paths.

Reads the list of changed files (one per line) from --files-from or, if omitted, runs
`git diff --name-only <base>...<head>`. The gate only applies to branches that start with
the agent prefix (default 'copilot/'); human branches pass untouched.

Where the rules come from: when the changed files come from git, the protected-path rules are read from
the BASE ref (`git show <base>:policy/protected-paths.txt`), never from the pull request's own checkout.
Otherwise a pull request could delete the rule that protects the rule file, and then change anything:
judged by its own edited rules, it would pass. With --files-from there is no base to read, so the rules
come from the working tree. (Found by running a pull request against the real gate. See docs/SANDBOX-LABS.md,
exercise 5.)

Exit codes: 0 = allowed (or not an agent branch), 1 = violation, 2 = bad usage or git error.

Examples:
    python scripts/policy_gate.py --branch copilot/fix-123 --base origin/main --head HEAD
    git diff --name-only main...feature | python scripts/policy_gate.py --branch copilot/x --files-from -

NOTE: this check reports a violation. Making it REQUIRED (so a merge is blocked) needs a
ruleset or branch protection. This repository does that: see rulesets/protect-main.json.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from labcommon import first_match, load_patterns, parse_patterns

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_PATH = ROOT / "policy" / "protected-paths.txt"
POLICY_RELATIVE = "policy/protected-paths.txt"


def find_violations(changed_files: list[str], protected: list[str]) -> list[tuple[str, str]]:
    """Return (file, matching rule) for every changed file that is protected.

    Changed files are concrete paths, so the plain canonical match is exactly right here (a broad
    scope is a PLAN-time idea; by diff time every file has a real name).
    """
    hits = []
    for name in changed_files:
        rule = first_match(name, protected)
        if rule:
            hits.append((name, rule))
    return hits


def is_agent_branch(branch: str, prefix: str) -> bool:
    return branch.startswith(prefix)


def _changed_files(base: str, head: str) -> list[str]:
    # --no-renames: with rename detection on, `git diff --name-only` reports only the NEW path, so a
    # protected file can be moved out of its folder without the gate ever seeing the old path.
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "diff", "--name-only", "--no-renames", f"{base}...{head}"],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git diff failed")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _protected_rules_at(ref: str) -> list[str]:
    """The protected-path rules as they are at `ref`, not as the change under review has them."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{POLICY_RELATIVE}"],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cannot read {POLICY_RELATIVE} at {ref}: {proc.stderr.strip() or 'git show failed'}")
    return parse_patterns(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branch", required=True, help="head branch name of the pull request")
    parser.add_argument("--agent-prefix", default="copilot/", help="branch prefix that marks an agent branch")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--files-from", help="file with changed paths, or '-' for stdin")
    args = parser.parse_args(argv)

    if not is_agent_branch(args.branch, args.agent_prefix):
        print(f"SKIP '{args.branch}' is not an agent branch ({args.agent_prefix}*); gate not applicable")
        return 0

    try:
        if args.files_from == "-":
            changed = [line.strip() for line in sys.stdin if line.strip()]
            protected = load_patterns(PROTECTED_PATH)
        elif args.files_from:
            changed = [line.strip() for line in Path(args.files_from).read_text(encoding="utf-8").splitlines() if line.strip()]
            protected = load_patterns(PROTECTED_PATH)
        else:
            changed = _changed_files(args.base, args.head)
            protected = _protected_rules_at(args.base)
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    hits = find_violations(changed, protected)
    if hits:
        print(f"FAIL agent branch '{args.branch}' changes protected paths:")
        for name, rule in hits:
            print(f"  - {name}  (rule: {rule})")
        print("A human must make these changes from a normal branch.")
        return 1
    print(f"PASS agent branch '{args.branch}' changed {len(changed)} file(s); none are protected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
