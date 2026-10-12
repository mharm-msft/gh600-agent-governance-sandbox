import json
import subprocess

import pytest

import approval_gate
from approval_gate import evaluate, latest_per_reviewer, main

HEAD = "a" * 40
OLD = "b" * 40


def review(login, state="APPROVED", commit=HEAD, assoc="MEMBER", at="2026-10-09T10:00:00Z", **author_extra):
    return {
        "author": {"login": login, **author_extra},
        "state": state,
        "commit": {"oid": commit},
        "authorAssociation": assoc,
        "submittedAt": at,
    }


def pr(*reviews, author="agent-user", head=HEAD):
    return {"author": {"login": author}, "headRefOid": head, "reviews": list(reviews)}


def test_a_current_approval_from_a_different_human_passes():
    ok, why = evaluate(pr(review("michael")))
    assert ok and "approved on aaaaaaa by @michael" in why[0]


def test_no_reviews_means_nobody_approved():
    ok, why = evaluate(pr())
    assert not ok and why == ["no human has approved this pull request yet"]


def test_the_author_cannot_approve_their_own_pull_request():
    ok, why = evaluate(pr(review("agent-user"), author="agent-user"))
    assert not ok and "is the author" in why[0]


def test_a_bot_approval_does_not_count_however_it_is_spelled():
    for author in (
        {"login": "copilot-pull-request-reviewer[bot]"},
        {"login": "app/dependabot", "is_bot": True},
        {"login": "x", "__typename": "Bot"},
    ):
        r = review("ignored")
        r["author"] = author
        ok, why = evaluate(pr(r))
        assert not ok and "is a bot" in why[0], author


def test_an_approval_of_an_older_commit_does_not_cover_code_pushed_afterwards():
    ok, why = evaluate(pr(review("michael", commit=OLD)))
    assert not ok and "approved commit bbbbbbb, but the pull request is now at aaaaaaa" in why[0]


def test_an_approval_from_someone_without_write_access_does_not_count():
    for assoc in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR"):
        ok, why = evaluate(pr(review("drive-by", assoc=assoc)))
        assert not ok and "not write access" in why[0], assoc


def test_an_unresolved_change_request_blocks_even_with_another_approval():
    ok, why = evaluate(pr(review("alice", "CHANGES_REQUESTED", at="2026-10-09T11:00:00Z"), review("bob", at="2026-10-09T12:00:00Z")))
    assert not ok and "@alice requested changes" in why[0]


def test_a_reviewer_who_later_approves_clears_their_own_change_request():
    ok, _ = evaluate(pr(review("alice", "CHANGES_REQUESTED", at="2026-10-09T11:00:00Z"), review("alice", at="2026-10-09T12:00:00Z")))
    assert ok


def test_a_later_comment_does_not_erase_an_approval():
    ok, _ = evaluate(pr(review("alice", at="2026-10-09T11:00:00Z"), review("alice", "COMMENTED", at="2026-10-09T12:00:00Z")))
    assert ok


def test_a_dismissed_approval_is_not_an_approval():
    ok, why = evaluate(pr(review("alice", "DISMISSED")))
    assert not ok and why == ["no human has approved this pull request yet"]


def test_one_good_approval_is_enough_even_if_another_does_not_count():
    ok, why = evaluate(pr(review("alice", commit=OLD), review("bob")))
    assert ok and "@bob" in why[0]


def test_an_unknown_head_commit_cannot_be_approved():
    ok, why = evaluate({"author": {"login": "x"}, "headRefOid": "", "reviews": [review("alice")]})
    assert not ok and "head commit is unknown" in why[0]


def test_latest_per_reviewer_keeps_only_the_newest_opinion():
    rows = latest_per_reviewer(
        [review("a", "CHANGES_REQUESTED", at="1"), review("a", "APPROVED", at="2"), review("b", "COMMENTED", at="3")]
    )
    assert [(r["author"]["login"], r["state"]) for r in rows] == [("a", "APPROVED")]


def test_the_real_gh_shape_is_accepted(tmp_path):
    """Shape captured from `gh pr view --json author,headRefOid,reviews` on a real merged pull request."""
    real = {
        "author": {"is_bot": True, "login": "app/dependabot"},
        "headRefOid": "424f38df651e0b178d752073859af7ee1d3d02c3",
        "reviews": [
            {
                "author": {"login": "williammartin"},
                "authorAssociation": "MEMBER",
                "commit": {"oid": "424f38df651e0b178d752073859af7ee1d3d02c3"},
                "state": "APPROVED",
                "submittedAt": "2026-10-09T14:33:25Z",
            }
        ],
    }
    ok, _ = evaluate(real)
    assert ok


def test_an_empty_commit_id_is_treated_as_unverifiable_not_as_a_match():
    """`gh pr view --json latestReviews` really returns commit.oid == '' (seen on a real PR)."""
    r = review("michael")
    r["commit"] = {"oid": ""}
    ok, why = evaluate(pr(r))
    assert not ok and "approved commit unknown" in why[0]


def test_the_graphql_shape_is_accepted():
    graphql = {
        "author": {"login": "dependabot", "__typename": "Bot"},
        "headRefOid": HEAD,
        "latestReviews": [review("michael", __typename="User")],
    }
    ok, _ = evaluate(graphql)
    assert ok


def test_cli_reads_a_file_and_sets_the_exit_code(tmp_path, capsys):
    good = tmp_path / "good.work.json"
    good.write_text(json.dumps(pr(review("michael"))), encoding="utf-8")
    assert main(["--pr-json", str(good)]) == 0
    assert "PASS approval gate" in capsys.readouterr().out

    bad = tmp_path / "bad.work.json"
    bad.write_text(json.dumps(pr()), encoding="utf-8")
    assert main(["--pr-json", str(bad)]) == 1
    assert "FAIL approval gate" in capsys.readouterr().out


def test_cli_reports_unreadable_input_as_exit_2(tmp_path, capsys):
    assert main(["--pr-json", str(tmp_path / "missing.json")]) == 2
    broken = tmp_path / "broken.work.json"
    broken.write_text("not json", encoding="utf-8")
    assert main(["--pr-json", str(broken)]) == 2
    arr = tmp_path / "arr.work.json"
    arr.write_text("[]", encoding="utf-8")
    assert main(["--pr-json", str(arr)]) == 2


def test_cli_needs_either_a_file_or_a_repo_and_number():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_gh_failure_is_reported_not_swallowed(monkeypatch, capsys):
    def boom(*a, **k):
        return subprocess.CompletedProcess(a, 1, stdout="", stderr="HTTP 404: Not Found")

    monkeypatch.setattr(approval_gate.subprocess, "run", boom)
    assert main(["--repo", "o/r", "--pr", "1"]) == 2
    assert "HTTP 404" in capsys.readouterr().err
