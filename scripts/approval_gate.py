"""Approval gate: has a HUMAN, other than the author, approved this pull request's CURRENT commit?

Why this exists. A plan file can carry an `approval` record, but the agent writes that file, so the
record can be forged. Real authorization has to come from something the agent cannot write. GitHub's
review record is that: the agent cannot approve its own pull request, and the cloud agent cannot
approve or merge at all.

Rules (all must hold):
  1. A review with state APPROVED exists from someone with WRITE access ...
  2. ... who is a person (not a bot) and not the pull request's author ...
  3. ... and the approval was given on the pull request's CURRENT head commit. An approval of an
     older commit says nothing about code pushed afterwards. (Branch protection can also dismiss
     stale approvals automatically, but that is an optional setting, so this gate checks it too.)
  4. No reviewer's latest review is CHANGES_REQUESTED.

Input: a JSON file in the shape `gh pr view --json author,headRefOid,reviews` produces, or the
GitHub GraphQL response for `latestOpinionatedReviews(writersOnly: true)`.

    gh pr view 123 --json author,headRefOid,reviews > pr.work.json
    python scripts/approval_gate.py --pr-json pr.work.json

Use `reviews`, not `latestReviews`: with `gh --json`, `latestReviews[].commit.oid` comes back EMPTY,
so an approval could not be tied to a commit. (Found by running this against a real pull request.)

Or let the script call gh itself:

    python scripts/approval_gate.py --repo OWNER/REPO --pr 123

Like the Plan Gate, this REPORTS. Only a required status check or required review in a ruleset or
branch protection makes it BLOCK a merge (see rulesets/protect-main.json).

Exit codes: 0 = approved, 1 = not approved, 2 = bad usage or unreadable input.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

WRITER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def _login(who: dict | None) -> str:
    return (who or {}).get("login", "") or ""


def _is_bot(who: dict | None) -> bool:
    who = who or {}
    return bool(who.get("is_bot")) or who.get("__typename") == "Bot" or _login(who).startswith("app/") or _login(who).endswith("[bot]")


def latest_per_reviewer(reviews: list[dict]) -> list[dict]:
    """Keep each reviewer's most recent opinionated review (APPROVED, CHANGES_REQUESTED or DISMISSED).

    A later COMMENTED review does not erase an earlier approval or change request, which matches how
    GitHub counts them. `latestReviews` from the API is already shaped like this; a raw `reviews`
    list is not, so both are accepted.
    """
    latest: dict[str, dict] = {}
    for review in sorted(reviews, key=lambda r: r.get("submittedAt") or ""):
        if review.get("state") not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            continue
        latest[_login(review.get("author"))] = review
    return list(latest.values())


def _commit(review: dict) -> str:
    commit = review.get("commit")
    return (commit or {}).get("oid", "") if isinstance(commit, dict) else (commit or "")


def evaluate(pr: dict) -> tuple[bool, list[str]]:
    """Return (approved, reasons). `reasons` explains the outcome either way, so a person can act on it."""
    author = _login(pr.get("author"))
    head = pr.get("headRefOid", "")
    reviews = latest_per_reviewer(pr.get("reviews") or pr.get("latestReviews") or [])
    if not head:
        return False, ["the pull request's current head commit is unknown, so no approval can be tied to it"]

    reasons: list[str] = []
    blockers = [r for r in reviews if r.get("state") == "CHANGES_REQUESTED"]
    for review in blockers:
        reasons.append(f"@{_login(review.get('author'))} requested changes and has not approved since")

    accepted: list[str] = []
    for review in reviews:
        if review.get("state") != "APPROVED":
            continue
        who = _login(review.get("author"))
        why_not = None
        if _is_bot(review.get("author")):
            why_not = "is a bot, not a person"
        elif who and who == author:
            why_not = "is the author; nobody approves their own work"
        elif review.get("authorAssociation") and review["authorAssociation"] not in WRITER_ASSOCIATIONS:
            why_not = f"has association {review['authorAssociation']}, which is not write access"
        elif _commit(review) != head:
            why_not = f"approved commit {(_commit(review) or 'unknown')[:7]}, but the pull request is now at {head[:7]}"
        if why_not:
            reasons.append(f"approval by @{who} does not count: it {why_not}")
        else:
            accepted.append(who)

    if accepted and not blockers:
        return True, [f"approved on {head[:7]} by @{', @'.join(accepted)}"]
    if not accepted and not reasons:
        reasons.append("no human has approved this pull request yet")
    return False, reasons


def _from_gh(repo: str, number: int) -> dict:
    # Ask for `reviews`, not `latestReviews`: with --json, `gh` fills `latestReviews[].commit.oid`
    # with an EMPTY string, so an approval could never be tied to a commit. `reviews` carries the real
    # id, and `latest_per_reviewer` does the "newest opinion per person" work itself.
    proc = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", repo, "--json", "author,headRefOid,reviews"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "gh pr view failed")
    return json.loads(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pr-json", type=Path, help="JSON file with author, headRefOid and reviews/latestReviews")
    parser.add_argument("--repo", help="OWNER/REPO, used with --pr to call gh")
    parser.add_argument("--pr", type=int, help="pull request number, used with --repo")
    args = parser.parse_args(argv)
    try:
        if args.pr_json:
            pr = json.loads(args.pr_json.read_text(encoding="utf-8"))
        elif args.repo and args.pr:
            pr = _from_gh(args.repo, args.pr)
        else:
            parser.error("provide --pr-json, or both --repo and --pr")
        if not isinstance(pr, dict):
            raise ValueError("input must be a JSON object")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    approved, reasons = evaluate(pr)
    print("PASS approval gate" if approved else "FAIL approval gate")
    for reason in reasons:
        print(f"  - {reason}")
    return 0 if approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
