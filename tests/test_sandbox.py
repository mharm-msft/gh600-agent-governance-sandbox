"""The sandbox's own invariants: the settings, the workflows and the policy must agree with each other.

Each test here guards a way this repository could look protected while not being protected.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

import lint_agent_config
import policy_gate
from labcommon import first_match, load_patterns

ROOT = Path(__file__).resolve().parents[1]
RULESET = json.loads((ROOT / "rulesets" / "protect-main.json").read_text(encoding="utf-8"))
PROTECTED = load_patterns(ROOT / "policy" / "protected-paths.txt")
FILTER_KEYS = {"paths", "paths-ignore", "branches", "branches-ignore"}


def workflow(name: str) -> dict:
    data = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))
    return data


def rule(kind: str) -> dict:
    found = [r for r in RULESET["rules"] if r["type"] == kind]
    assert len(found) == 1, f"expected exactly one '{kind}' rule"
    return found[0].get("parameters", {})


def check_names() -> dict[str, list[tuple[str, str]]]:
    """The name a required check must use is the JOB name (or the job id when it has no name)."""
    names: dict[str, list[tuple[str, str]]] = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for job_id, job in workflow(path.name)["jobs"].items():
            names.setdefault(job.get("name", job_id), []).append((path.name, job_id))
    return names


REQUIRED = [c["context"] for c in rule("required_status_checks")["required_status_checks"]]


# ---------- required checks must be able to report ----------

@pytest.mark.parametrize("context", REQUIRED)
def test_each_required_check_is_the_name_of_exactly_one_job(context):
    """A required name that matches no job is 'Expected - waiting for status' forever, so every pull request is blocked."""
    assert len(check_names().get(context, [])) == 1, f"{context!r} must name one job; jobs are {sorted(check_names())}"


@pytest.mark.parametrize("context", REQUIRED)
def test_each_required_check_runs_on_every_pull_request(context):
    """A paths or branches filter on the pull_request trigger leaves the check unreported on other pull requests."""
    (file, _), = check_names()[context]
    trigger = workflow(file)["on"]
    assert "pull_request" in trigger, f"{file} must run on pull_request"
    assert not FILTER_KEYS & set(trigger["pull_request"] or {}), f"{file} filters its pull_request trigger"


def test_the_plan_gate_reruns_when_the_description_changes_or_a_commit_is_pushed():
    types = set(workflow("plan-gate.yml")["on"]["pull_request"]["types"])
    assert {"opened", "edited", "synchronize", "reopened"} <= types


def test_the_plan_gate_only_applies_to_the_branch_prefix_the_policy_gate_uses():
    condition = workflow("plan-gate.yml")["jobs"]["require-plan"]["if"]
    assert "startsWith(github.head_ref, 'copilot/')" in condition
    assert 'default="copilot/"' in (ROOT / "scripts" / "policy_gate.py").read_text(encoding="utf-8")
    assert policy_gate.is_agent_branch("copilot/fix-1", "copilot/")


def test_a_skipped_gate_job_is_the_design_not_an_accident():
    """The job-level `if:` makes the check report 'skipped' on human branches. Keep that comment where a reader will see it."""
    text = (ROOT / ".github" / "workflows" / "plan-gate.yml").read_text(encoding="utf-8")
    assert "skipped" in text and "paths:" in text


# ---------- the ruleset ----------

def test_the_ruleset_is_active_and_covers_the_default_branch():
    assert RULESET["enforcement"] == "active"
    assert RULESET["target"] == "branch"
    assert RULESET["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]


def test_the_ruleset_blocks_deletion_and_force_push():
    rule("deletion")
    rule("non_fast_forward")


def test_the_ruleset_requires_a_reviewed_pull_request_with_code_owner_review():
    params = rule("pull_request")
    assert params["required_approving_review_count"] >= 1
    assert params["require_code_owner_review"] is True
    assert params["dismiss_stale_reviews_on_push"] is True, "an approval of old code must not cover new commits"
    assert params["require_last_push_approval"] is True, "the person who pushed last cannot be the approver"


def test_the_required_checks_are_pinned_to_the_github_actions_app():
    """Without integration_id, any app (or a plain commit status) that posts a check named 'validate' satisfies it."""
    for check in rule("required_status_checks")["required_status_checks"]:
        assert isinstance(check.get("integration_id"), int), check


def test_the_bypass_is_pull_request_only_so_a_direct_push_stays_blocked():
    actors = RULESET["bypass_actors"]
    assert actors, "a sole maintainer cannot approve their own pull request, so one auditable bypass path is needed"
    assert {a["bypass_mode"] for a in actors} == {"pull_request"}, "'always' would also allow direct pushes to main"


# ---------- CODEOWNERS and the protected paths ----------

def owners_patterns(text: str) -> list[str]:
    lines = [ln.split()[0] for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return [p for p in lines if p != "*"]


def literal_prefix(rule_text: str) -> str:
    for index, char in enumerate(rule_text):
        if char in "*?[":
            return rule_text[:index]
    return rule_text


def covered(rule_text: str, patterns: list[str]) -> bool:
    anchored = "/" + literal_prefix(rule_text)
    return any(anchored.startswith(p) if p.endswith("/") else anchored == p for p in patterns)


def test_every_protected_path_has_an_explicit_code_owner_rule():
    """The catch-all '*' owner would hide a missing rule: the day the catch-all changes, a protected path loses its reviewer."""
    patterns = owners_patterns((ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8"))
    missing = [r for r in PROTECTED if not covered(r, patterns)]
    assert missing == [], f"protected rules with no explicit CODEOWNERS rule: {missing}"


def test_the_coverage_check_itself_can_fail():
    assert covered("scripts/**", ["/scripts/"])
    assert covered(".github/CODEOWNERS", ["/.github/"])
    assert covered("AGENTS.md", ["/AGENTS.md"])
    assert not covered("scripts/**", ["/script/", "/docs/"])
    assert not covered("rulesets/**", ["/.github/", "/scripts/"])


def test_the_rule_that_defines_the_rules_is_itself_protected():
    assert "rulesets/**" in PROTECTED and "policy/**" in PROTECTED and ".github/CODEOWNERS" in PROTECTED


def test_the_hook_folder_is_protected_because_a_hook_is_an_enforcement_layer():
    """An agent that can add or edit a hook can change what is checked inside its own session."""
    assert first_match(".github/hooks/guardrails.json", PROTECTED) == ".github/hooks/**"


# ---------- the files agree with each other ----------

def test_the_demo_workflow_targets_the_environment_the_settings_script_creates():
    import sandbox_settings

    job = workflow("deploy-demo.yml")["jobs"]["deploy"]
    assert job["environment"] == sandbox_settings.ENVIRONMENT


def test_the_environment_only_lets_main_deploy():
    import sandbox_settings

    steps = sandbox_settings.build_steps("o/r", 1, RULESET)
    environment = next(s for s in steps if s.method == "PUT" and "/environments/" in s.path)
    assert environment.body["deployment_branch_policy"] == {"protected_branches": False, "custom_branch_policies": True}
    assert any(s.body == {"name": "main", "type": "branch"} for s in steps)


# ---------- agent customization files ----------

@pytest.mark.parametrize("name", ["plan-only-planner.agent.md", "scoped-implementer.agent.md"])
def test_shipped_agents_have_no_lint_errors(name):
    path = ROOT / ".github" / "agents" / name
    findings = lint_agent_config.lint_agent(path.name, path.read_text(encoding="utf-8"))
    assert [f.message for f in findings if f.severity == "ERROR"] == []


def test_the_default_lint_run_on_this_repository_is_clean(capsys):
    assert lint_agent_config.main([]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_the_hook_example_is_clean_and_not_live():
    path = ROOT / "examples" / "hooks" / "guardrails.hooks.example.json"
    assert lint_agent_config.lint_hooks("hooks.json", path.read_text(encoding="utf-8")) == []
    assert not (ROOT / ".github" / "hooks").exists(), "hooks are activated on purpose, in a reviewed pull request (exercise 8)"


# ---------- documentation and hygiene ----------

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def markdown_files() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: p.name)
def test_relative_links_point_at_files_that_exist(path):
    broken = []
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        if not (path.parent / target.split("#")[0]).resolve().exists():
            broken.append(target)
    assert broken == [], f"{path.name} links to missing files: {broken}"


SKIPPED_DIRS = {".git", "__pycache__", ".pytest_cache", "reports", "logs"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ALLOWED_EMAIL_DOMAINS = {"users.noreply.github.com", "example.com", "example.org", "example.invalid"}
SSH_REMOTE = "git@github.com"  # the SSH form of a repository URL, not an email address
LOCAL_PATH = re.compile(r"[A-Za-z]:\\Users\\|/Users/\w|OneDrive")


def text_files() -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not SKIPPED_DIRS & set(path.relative_to(ROOT).parts) and path != Path(__file__).resolve():
            found.append(path)
    return found


def test_no_personal_email_or_local_path_is_in_the_repository():
    problems = []
    for path in text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for email in EMAIL.findall(line):
                if email.lower() != SSH_REMOTE and email.split("@")[1].lower() not in ALLOWED_EMAIL_DOMAINS:
                    problems.append(f"{path.relative_to(ROOT).as_posix()}:{number}: email {email}")
            if LOCAL_PATH.search(line):
                problems.append(f"{path.relative_to(ROOT).as_posix()}:{number}: local path")
    assert problems == []
