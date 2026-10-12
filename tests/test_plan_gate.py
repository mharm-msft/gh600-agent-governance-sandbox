"""The Plan Gate reads the pull request BODY, so these tests feed it bodies and check the verdict.

Run by CI on every pull request. The gate itself only runs on copilot/* branches (see
.github/workflows/plan-gate.yml); the tests here prove what it decides when it does run.
"""

from pathlib import Path

from plan_gate import boilerplate_from, check_body, load_template_boilerplate, sections

ROOT = Path(__file__).resolve().parents[1]

GOOD_BODY = """
## Plan (required)
- **Goal:** Add a discontinued flag to inventory items
- **Scope (paths/files):** sample-service/src/inventory/*.py only
- **Steps:**
  1. Add the field
  2. Add discontinue()
- **Success criteria (verifiable):**
  - [x] Acceptance tests pass
- **Risks + mitigations:** Low; default value keeps callers working
- **Rollback / escalation plan:** Revert this PR

## Evidence
- Workflow run(s): https://github.com/example/repo/actions/runs/123
"""

TEMPLATE_BODY = """
## Plan (required)
- **Goal:**
- **Scope (paths/files):**
- **Steps:**
  1.
  2.
  3.
- **Success criteria (verifiable):**
  - [ ] Required checks pass
- **Risks + mitigations:**
- **Rollback / escalation plan:**

## Evidence
- Workflow run(s):
- Scan results (if applicable):
"""


def test_a_filled_in_plan_passes():
    assert check_body(GOOD_BODY, boilerplate_from(TEMPLATE_BODY)) == []


def test_the_unfilled_template_fails_on_every_field_not_just_the_obvious_ones():
    problems = check_body(TEMPLATE_BODY, boilerplate_from(TEMPLATE_BODY))
    for field in ("Goal", "Scope", "Steps", "Success criteria", "Risks", "Rollback"):
        assert any(f"'{field}'" in p for p in problems), f"{field} slipped through the unfilled template"
    assert any("Evidence" in p for p in problems)
    assert len(problems) == 7


def test_the_repos_real_pr_template_fails_the_gate_when_left_unfilled():
    real = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    problems = check_body(real)
    assert len(problems) == 7, problems


def test_the_stock_checklist_alone_does_not_count_as_success_criteria():
    body = GOOD_BODY.replace("  - [x] Acceptance tests pass", "  - [x] Required checks pass")
    problems = check_body(body, boilerplate_from(TEMPLATE_BODY))
    assert problems == ["plan field 'Success criteria' only repeats the pull request template; write your own"]


def test_own_criteria_written_beside_the_stock_checklist_pass():
    body = GOOD_BODY.replace(
        "  - [x] Acceptance tests pass",
        "  - [x] Required checks pass\n  - [x] Acceptance tests pass",
    )
    assert check_body(body, boilerplate_from(TEMPLATE_BODY)) == []


def test_ticking_or_unticking_or_restyling_a_stock_line_does_not_make_it_content():
    for variant in ("- [x] required checks pass", "* [X]   Required  Checks Pass", "1. [ ] REQUIRED CHECKS PASS"):
        body = GOOD_BODY.replace("  - [x] Acceptance tests pass", f"  {variant}")
        assert check_body(body, boilerplate_from(TEMPLATE_BODY)), variant


def test_a_body_without_a_plan_section_fails():
    assert "no '## Plan' section" in check_body("Just a diff, trust me.")[0]


def test_html_comments_do_not_count_as_content():
    body = GOOD_BODY.replace("Add a discontinued flag to inventory items", "<!-- describe the goal -->")
    assert any("'Goal'" in p for p in check_body(body, boilerplate_from(TEMPLATE_BODY)))


def test_missing_evidence_link_is_reported():
    body = GOOD_BODY.replace("https://github.com/example/repo/actions/runs/123", "see CI")
    assert any("Evidence" in p for p in check_body(body, boilerplate_from(TEMPLATE_BODY)))


def test_a_missing_template_file_warns_instead_of_failing_silently(tmp_path, capsys):
    assert load_template_boilerplate(tmp_path / "nope.md") == frozenset()
    assert "warning: PR template not found" in capsys.readouterr().err


def test_sections_ignores_the_required_suffix():
    assert "plan" in sections("## Plan (required)\ntext")
