"""The gate output quoted in docs/SANDBOX-LABS.md must be what the gates really print.

The document quotes real output. These tests re-run the scripts and fail if a quote drifts. Lines that come from
GitHub itself (push rejections, merge refusals, API errors) cannot be re-run here, so the document dates them instead.
"""

from pathlib import Path

import approval_gate
import plan_gate
import policy_gate

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "SANDBOX-LABS.md").read_text(encoding="utf-8")


def quoted(block: str) -> bool:
    return block.strip() in DOC


def run_policy_gate(tmp_path, capsys, branch: str, changed: list[str]) -> tuple[int, str]:
    listing = tmp_path / "changed.txt"
    listing.write_text("\n".join(changed) + "\n", encoding="utf-8")
    code = policy_gate.main(["--branch", branch, "--files-from", str(listing)])
    return code, capsys.readouterr().out


def test_the_plan_gate_message_for_the_untouched_template_is_quoted_exactly():
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    problems = plan_gate.check_body(template)
    assert len(problems) == 7
    assert quoted("FAIL plan gate\n" + "\n".join(f"  - {p}" for p in problems))


def test_the_filled_plan_fixture_passes_the_plan_gate():
    """Exercises 2, 3 and 5 rely on this file being a plan the gate accepts."""
    body = (ROOT / "exercises" / "pr-body-filled.md").read_text(encoding="utf-8")
    assert plan_gate.check_body(body, plan_gate.load_template_boilerplate()) == []


def test_exercise_3_part_a_policy_gate_message_is_quoted_exactly(tmp_path, capsys):
    code, out = run_policy_gate(tmp_path, capsys, "copilot/ex3-protected",
                                [".github/copilot-instructions.md", "sample-service/src/inventory/store.py"])
    assert code == 1 and quoted(out)


def test_exercise_3_part_b_the_human_branch_is_skipped_with_the_quoted_message(tmp_path, capsys):
    code, out = run_policy_gate(tmp_path, capsys, "feature/ex3-human", [".github/copilot-instructions.md"])
    assert code == 0 and quoted(out)


def test_exercise_5_part_a_policy_gate_message_is_quoted_exactly(tmp_path, capsys):
    code, out = run_policy_gate(tmp_path, capsys, "copilot/ex5-policy", ["policy/protected-paths.txt", "scripts/labcommon.py"])
    assert code == 1 and quoted(out)


def test_exercise_6_approval_gate_messages_are_quoted_exactly():
    head = "424f38d" + "0" * 33
    none = {"author": {"login": "someone"}, "headRefOid": head, "reviews": []}
    approved, reasons = approval_gate.evaluate(none)
    assert not approved and quoted("FAIL approval gate\n" + "\n".join(f"  - {r}" for r in reasons))

    review = {"state": "APPROVED", "author": {"login": "williammartin"}, "authorAssociation": "MEMBER",
              "commit": {"oid": head}, "submittedAt": "2026-01-01T00:00:00Z"}
    approved, reasons = approval_gate.evaluate({**none, "reviews": [review]})
    assert approved and quoted("PASS approval gate\n" + "\n".join(f"  - {r}" for r in reasons))


def test_the_documented_exercise_branches_match_the_names_the_gate_keys_on():
    """Every exercise branch the document says an agent would use must start with the agent prefix, and every human one must not."""
    import re

    for name in set(re.findall(r"copilot/ex[\w-]+", DOC)):
        assert policy_gate.is_agent_branch(name, "copilot/"), name
    for name in set(re.findall(r"feature/ex[\w-]+", DOC)):
        assert not policy_gate.is_agent_branch(name, "copilot/"), name
