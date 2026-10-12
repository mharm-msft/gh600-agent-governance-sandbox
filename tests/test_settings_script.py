import json
from io import StringIO

import pytest

import sandbox_settings as ss

REPO = "someone/sandbox"
RULESET = json.loads(ss.RULESET_FILE.read_text(encoding="utf-8"))


class FakeGh:
    """Stands in for `gh api`: records every call and answers from a small table."""

    def __init__(self, rulesets=None, policies=None, user_id="42", fail_on=None):
        self.calls = []
        self.rulesets = rulesets or []
        self.policies = policies or []
        self.user_id = user_id
        self.fail_on = fail_on

    def __call__(self, args, body):
        self.calls.append((list(args), body))
        joined = " ".join(args)
        if self.fail_on and self.fail_on in joined:
            return 1, "HTTP 403: nope"
        if joined.startswith("users/"):
            return 0, self.user_id
        if joined == f"repos/{REPO}/rulesets":
            return 0, json.dumps(self.rulesets)
        if joined.endswith("/deployment-branch-policies") and "--method" not in args:
            return 0, json.dumps({"branch_policies": self.policies})
        return 0, ""

    def writes(self):
        return [(a, b) for a, b in self.calls if "--method" in a]


def run(argv, gh):
    out = StringIO()
    return ss.main(argv, run=gh, out=out), out.getvalue()


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/someone/sandbox.git\n", "someone/sandbox"),
        ("https://github.com/someone/sandbox", "someone/sandbox"),
        ("https://github.com/someone/sandbox/", "someone/sandbox"),
        ("git@github.com:someone/sandbox.git", "someone/sandbox"),
        ("https://example.com/someone/sandbox.git", None),
        ("", None),
    ],
)
def test_the_repository_is_read_from_the_remote_url(url, expected):
    assert ss.parse_repo(url) == expected


def test_a_dry_run_makes_no_calls_and_says_so():
    gh = FakeGh()
    code, text = run(["--repo", REPO], gh)
    assert code == 0 and gh.calls == []
    assert text.startswith("DRY RUN. Nothing was changed.")
    for group in ss.GROUPS:
        assert f"[{group}]" in text


def test_applying_is_never_the_default():
    """No flag, no matter which groups are asked for, may reach the API."""
    gh = FakeGh()
    for extra in ([], ["--only", "ruleset"], ["--only", "environment", "--only", "actions"], ["--reviewer", "x"]):
        assert run(["--repo", REPO, *extra], gh)[0] == 0
    assert gh.calls == []


def test_apply_runs_the_groups_in_order_and_the_ruleset_goes_last():
    gh = FakeGh()
    code, text = run(["--repo", REPO, "--apply"], gh)
    assert code == 0
    groups = [line.split("]")[0].split("[")[1] for line in text.splitlines() if line.startswith("OK")]
    assert groups == ["repo", "actions", "actions", "environment", "environment", "ruleset"]


def test_apply_sends_the_ruleset_file_unchanged():
    gh = FakeGh()
    run(["--repo", REPO, "--apply"], gh)
    args, body = [w for w in gh.writes() if w[0][-1].endswith("/rulesets")][0]
    assert args[:2] == ["--method", "POST"] and body == RULESET


def test_an_existing_ruleset_is_updated_not_duplicated():
    gh = FakeGh(rulesets=[{"id": 7, "name": "protect-main"}, {"id": 8, "name": "other"}])
    run(["--repo", REPO, "--apply"], gh)
    ruleset_writes = [a for a, _ in gh.writes() if "rulesets" in a[-1]]
    assert ruleset_writes == [["--method", "PUT", f"repos/{REPO}/rulesets/7"]]


def test_an_existing_branch_policy_is_skipped():
    gh = FakeGh(policies=[{"name": "main"}])
    code, text = run(["--repo", REPO, "--apply"], gh)
    assert code == 0 and "SKIP [environment] already present" in text
    assert not [a for a, _ in gh.writes() if a[-1].endswith("/deployment-branch-policies")]


def test_the_reviewer_is_looked_up_and_used():
    gh = FakeGh(user_id="9001")
    run(["--repo", REPO, "--apply", "--reviewer", "other-person"], gh)
    assert gh.calls[0][0] == ["users/other-person", "--jq", ".id"]
    environment = [b for a, b in gh.writes() if "/environments/production" in a[-1] and not a[-1].endswith("policies")][0]
    assert environment["reviewers"] == [{"type": "User", "id": 9001}]


def test_only_limits_the_groups_and_skips_the_reviewer_lookup_when_not_needed():
    gh = FakeGh()
    code, text = run(["--repo", REPO, "--apply", "--only", "actions"], gh)
    assert code == 0
    assert [a[-1] for a, _ in gh.writes()] == [f"repos/{REPO}/actions/permissions/workflow", f"repos/{REPO}/actions/permissions/fork-pr-contributor-approval"]
    assert not any(a[0].startswith("users/") for a, _ in gh.calls)


def test_a_failing_call_stops_the_run_and_says_what_was_not_applied():
    gh = FakeGh(fail_on="fork-pr-contributor-approval")
    code, text = run(["--repo", REPO, "--apply"], gh)
    assert code == 1
    assert "FAIL [actions] PUT /repos/someone/sandbox/actions/permissions/fork-pr-contributor-approval" in text
    assert "Nothing after this step was applied." in text
    assert not [a for a, _ in gh.writes() if "rulesets" in a[-1]], "the ruleset must not be applied after a failure"


def test_a_failed_reviewer_lookup_changes_nothing():
    gh = FakeGh(user_id="not-a-number")
    code, _ = run(["--repo", REPO, "--apply"], gh)
    assert code == 1 and gh.writes() == []


def test_a_missing_repository_is_a_usage_error(monkeypatch):
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: type("P", (), {"returncode": 1, "stdout": ""})())
    assert run([], FakeGh())[0] == 2


def test_the_workflow_token_is_made_read_only_and_cannot_approve_pull_requests():
    step = next(s for s in ss.build_steps(REPO, 1, RULESET) if s.path.endswith("/actions/permissions/workflow"))
    assert step.body == {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False}


def test_the_plan_is_pure_data_so_it_can_be_reviewed_without_a_network():
    steps = ss.build_steps(REPO, None, RULESET)
    assert [s.group for s in steps] == ["repo", "actions", "actions", "environment", "environment", "ruleset"]
    assert all(s.path.startswith(f"/repos/{REPO}") for s in steps)


def test_a_solo_maintainer_can_approve_their_own_deployment_unless_asked_not_to():
    """prevent_self_review needs a second reviewer to be usable, so it is off by default and opt-in."""
    def flag(argv):
        gh = FakeGh()
        run(["--repo", REPO, "--apply", "--only", "environment", *argv], gh)
        body = [b for a, b in gh.writes() if "/environments/production" in a[-1] and not a[-1].endswith("policies")][0]
        return body["prevent_self_review"]

    assert flag([]) is False
    assert flag(["--prevent-self-review"]) is True
