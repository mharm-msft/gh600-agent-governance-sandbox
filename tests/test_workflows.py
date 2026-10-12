"""The repository's own workflows must follow the rules the exercises teach.

Scope the token, keep evidence reviewable, and never let a workflow in this sandbox reach anything real.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 reads the bare key `on` as the boolean True, which is what PyYAML does with GitHub's trigger key.
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_there_are_workflows_to_check():
    assert [p.name for p in WORKFLOWS] == ["copilot-setup-steps.yml", "deploy-demo.yml", "plan-gate.yml", "validate.yml"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_grants_the_token_read_access_only(path):
    workflow = load(path)
    scopes = [workflow.get("permissions")] + [job.get("permissions") for job in workflow["jobs"].values()]
    declared = [s for s in scopes if s is not None]
    assert declared, f"{path.name} never declares permissions, so it inherits the repository default"
    for scope in declared:
        assert scope != "write-all", path.name
        assert isinstance(scope, dict), path.name
        assert set(scope.values()) <= {"read", "none"}, f"{path.name} asks for write access: {scope}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_uses_a_secret_other_than_the_built_in_token(path):
    text = path.read_text(encoding="utf-8")
    assert "secrets." not in text.replace("secrets.GITHUB_TOKEN", "")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_runs_on_pull_request_target(path):
    """pull_request_target runs with a privileged token against code a stranger controls. It is never needed here."""
    assert "pull_request_target" not in load(path)["on"]


def test_every_job_has_a_timeout_well_under_the_six_hour_default():
    """A hung step must end the job, not burn a runner for six hours."""
    for path in WORKFLOWS:
        for name, job in load(path)["jobs"].items():
            minutes = job.get("timeout-minutes")
            assert isinstance(minutes, int) and 1 <= minutes <= 30, f"{path.name}:{name} timeout-minutes is {minutes!r}"


def test_the_evidence_upload_cannot_silently_upload_nothing():
    """upload-artifact skips hidden files by default, so uploading .pytest_cache uploaded nothing and said nothing."""
    steps = load(ROOT / ".github" / "workflows" / "validate.yml")["jobs"]["validate"]["steps"]
    uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    paths = [p.strip() for p in str(upload["with"]["path"]).splitlines() if p.strip()]
    assert paths and not any(part.startswith(".") for p in paths for part in Path(p).parts), paths


def test_the_evidence_the_upload_looks_for_is_what_the_test_steps_produce():
    steps = load(ROOT / ".github" / "workflows" / "validate.yml")["jobs"]["validate"]["steps"]
    commands = "\n".join(str(s.get("run", "")) for s in steps)
    assert "--junitxml=reports/script-tests.xml" in commands
    assert "--junitxml=../reports/sample-service-baseline.xml" in commands, "run from sample-service, so one level up"
    upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["with"]["path"].rstrip("/") == "reports"
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "reports/" in ignored, "local runs that write reports must not leave files to commit"


def test_the_deploy_demo_can_only_be_started_by_hand_and_only_prints_text():
    workflow = load(ROOT / ".github" / "workflows" / "deploy-demo.yml")
    assert list(workflow["on"]) == ["workflow_dispatch"], "a pull request or a push must never start a deployment"
    job = workflow["jobs"]["deploy"]
    assert job["environment"] == "production"
    for step in job["steps"]:
        assert "uses" not in step, "the demo deploys nothing, so it needs no action"
        for line in str(step.get("run", "")).splitlines():
            assert line.strip().startswith("echo "), f"the demo may only echo, found: {line!r}"

