import subprocess
from pathlib import Path

import pytest

import policy_gate
from labcommon import first_match, glob_to_regex, load_patterns, normalize
from policy_gate import find_violations, is_agent_branch

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = load_patterns(ROOT / "policy" / "protected-paths.txt")


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("docs/**", "docs/a/b.md", True),
        ("docs/**", "docsx/a.md", False),
        ("**/*.py", "a.py", True),
        ("**/*.py", "x/y/a.py", True),
        (".github/workflows/**", ".github/workflows/ci.yml", True),
        ("a?c", "abc", True),
        ("a?c", "a/c", False),
    ],
)
def test_glob_to_regex(pattern, path, expected):
    assert bool(glob_to_regex(pattern).match(path)) is expected


def test_normalize_keeps_leading_dot_folders_but_drops_dot_slash():
    assert normalize("./src/a.py") == "src/a.py"
    assert normalize(".github\\workflows\\ci.yml") == ".github/workflows/ci.yml"


def test_first_match_returns_the_rule_that_matched():
    assert first_match(".github/workflows/ci.yml", PROTECTED) == ".github/workflows/**"
    assert first_match("sample-service/src/inventory/store.py", PROTECTED) is None


def test_agent_branch_detection():
    assert is_agent_branch("copilot/fix-1", "copilot/")
    assert not is_agent_branch("feature/copilot/fix", "copilot/")


def test_find_violations_flags_only_protected_files():
    changed = ["sample-service/src/inventory/store.py", "policy/protected-paths.txt", ".github/agents/x.agent.md", "README.md"]
    hits = dict(find_violations(changed, PROTECTED))
    assert set(hits) == {"policy/protected-paths.txt", ".github/agents/x.agent.md"}


def test_an_agent_branch_that_deletes_or_edits_the_acceptance_tests_fails_the_gate():
    """`git diff --name-only` lists deleted files too, so deleting the failing test is caught."""
    changed = ["sample-service/src/inventory/store.py", "sample-service/acceptance/test_discontinue.py", "plans/good-plan.json"]
    hits = dict(find_violations(changed, PROTECTED))
    assert hits == {
        "sample-service/acceptance/test_discontinue.py": "sample-service/acceptance/**",
        "plans/good-plan.json": "plans/**",
    }


def test_a_normal_task_a_change_set_passes_the_gate():
    changed = ["sample-service/src/inventory/models.py", "sample-service/src/inventory/store.py", "sample-service/tests/test_discontinue.py"]
    assert find_violations(changed, PROTECTED) == []


def test_load_patterns_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("# comment\n\n a/** \nb\n", encoding="utf-8")
    assert load_patterns(f) == ["a/**", "b"]


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def test_moving_a_protected_file_out_of_its_folder_is_still_caught(tmp_path, monkeypatch):
    """The blind spot behind `--no-renames`: rename detection reports only the NEW path."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "lab@example.invalid")
    git(tmp_path, "config", "user.name", "lab")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("print('guard')\n" * 20, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "copilot/x")
    (tmp_path / "sample-service").mkdir()
    git(tmp_path, "mv", "scripts/a.py", "sample-service/a.py")
    git(tmp_path, "commit", "-q", "-m", "move it out of the protected folder")

    # The control: with rename detection on, git hides the protected path, so a gate built on it is blind.
    hidden = git(tmp_path, "-c", "diff.renames=true", "diff", "--name-only", "main...HEAD").split()
    assert hidden == ["sample-service/a.py"]
    assert find_violations(hidden, PROTECTED) == []

    monkeypatch.setattr(policy_gate, "ROOT", tmp_path)
    changed = policy_gate._changed_files("main", "HEAD")
    assert set(changed) == {"scripts/a.py", "sample-service/a.py"}
    assert dict(find_violations(changed, PROTECTED)) == {"scripts/a.py": "scripts/**"}


RULES = "# protected\nscripts/**\npolicy/**\n.github/workflows/**\n"


def repo_with_rules(tmp_path, monkeypatch, rules=RULES):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "lab@example.invalid")
    git(tmp_path, "config", "user.name", "lab")
    git(tmp_path, "config", "commit.gpgsign", "false")
    files = {"scripts/a.py": "print('guard')\n", ".github/workflows/ci.yml": "name: ci\n"}
    if rules is not None:
        files["policy/protected-paths.txt"] = rules
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.setattr(policy_gate, "ROOT", tmp_path)
    monkeypatch.setattr(policy_gate, "PROTECTED_PATH", tmp_path / "policy" / "protected-paths.txt")


def test_a_pull_request_cannot_weaken_the_rules_that_judge_it(tmp_path, monkeypatch, capsys):
    """Found by running a pull request against the real gate: the rules were read from the pull request's own
    checkout, so a change that emptied policy/protected-paths.txt and edited a workflow in the same commit passed."""
    repo_with_rules(tmp_path, monkeypatch)
    git(tmp_path, "checkout", "-q", "-b", "copilot/x")
    (tmp_path / "policy" / "protected-paths.txt").write_text("# nothing is protected any more\n", encoding="utf-8")
    (tmp_path / "scripts" / "a.py").write_text("print('guard removed')\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n# edited\n", encoding="utf-8")
    git(tmp_path, "commit", "-q", "-am", "weaken the rules and edit what they protect")

    # The control: judged by the pull request's own rules, the same change is clean.
    changed = policy_gate._changed_files("main", "HEAD")
    assert find_violations(changed, load_patterns(tmp_path / "policy" / "protected-paths.txt")) == []

    assert policy_gate.main(["--branch", "copilot/x", "--base", "main", "--head", "HEAD"]) == 1
    out = capsys.readouterr().out
    for name in ("policy/protected-paths.txt", "scripts/a.py", ".github/workflows/ci.yml"):
        assert name in out


def test_a_human_branch_is_still_not_judged(tmp_path, monkeypatch, capsys):
    repo_with_rules(tmp_path, monkeypatch)
    git(tmp_path, "checkout", "-q", "-b", "feature/x")
    (tmp_path / "scripts" / "a.py").write_text("print('human edit')\n", encoding="utf-8")
    git(tmp_path, "commit", "-q", "-am", "a person edits a protected file")
    assert policy_gate.main(["--branch", "feature/x", "--base", "main", "--head", "HEAD"]) == 0
    assert capsys.readouterr().out.startswith("SKIP")


def test_a_base_with_no_rules_file_fails_closed_instead_of_passing_everything(tmp_path, monkeypatch, capsys):
    repo_with_rules(tmp_path, monkeypatch, rules=None)
    git(tmp_path, "checkout", "-q", "-b", "copilot/x")
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy" / "protected-paths.txt").write_text("# the pull request brings its own, empty, rules\n", encoding="utf-8")
    (tmp_path / "scripts" / "a.py").write_text("print('edited')\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "add empty rules and edit a script")
    assert policy_gate.main(["--branch", "copilot/x", "--base", "main", "--head", "HEAD"]) == 2
    assert "cannot read policy/protected-paths.txt at main" in capsys.readouterr().err


def test_a_changed_file_list_from_outside_git_is_judged_by_the_working_tree_rules(tmp_path, monkeypatch, capsys):
    repo_with_rules(tmp_path, monkeypatch)
    listing = tmp_path / "changed.txt"
    listing.write_text("scripts/a.py\n", encoding="utf-8")
    assert policy_gate.main(["--branch", "copilot/x", "--files-from", str(listing)]) == 1
    assert "scripts/a.py" in capsys.readouterr().out
