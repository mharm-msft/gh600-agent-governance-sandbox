import itertools
import random
from pathlib import Path

import pytest

from labcommon import (
    canonical,
    first_match,
    glob_to_regex,
    globs_can_overlap,
    load_patterns,
    reaches_protected,
    writes_into_protected,
)

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = load_patterns(ROOT / "policy" / "protected-paths.txt")


@pytest.mark.parametrize(
    "path, expected",
    [
        ("scripts/validate_plan.py", "scripts/validate_plan.py"),
        ("./scripts/../scripts/validate_plan.py", "scripts/validate_plan.py"),
        ("sample-service/../scripts/validate_plan.py", "scripts/validate_plan.py"),
        ("scripts//validate_plan.py", "scripts/validate_plan.py"),
        ("scripts/./validate_plan.py", "scripts/validate_plan.py"),
        ("Scripts\\Validate_Plan.PY", "scripts/validate_plan.py"),
        (".GITHUB/Workflows/ci.yml", ".github/workflows/ci.yml"),
        ("/workspace/scripts/x.py", "scripts/x.py"),
        ("/github/workspace/scripts/x.py", "scripts/x.py"),
        ("/workspace/sample-service/../scripts/x.py", "scripts/x.py"),
        ("'scripts/x.py'", "scripts/x.py"),
        (".", ""),
        ("", ""),
        ("./", ""),
        ("../outside.txt", "../outside.txt"),
        ("a/../../outside.txt", "../outside.txt"),
    ],
)
def test_canonical_gives_one_spelling_per_file(path, expected):
    assert canonical(path) == expected


@pytest.mark.parametrize(
    "spelling",
    [
        "scripts/validate_plan.py",
        "sample-service/../scripts/validate_plan.py",
        "./scripts/../scripts/validate_plan.py",
        "scripts//validate_plan.py",
        "scripts/./validate_plan.py",
        "Scripts/validate_plan.py",
        "SCRIPTS/VALIDATE_PLAN.PY",
        "scripts\\validate_plan.py",
        "/workspace/scripts/validate_plan.py",
        "/workspace/sample-service/../scripts/validate_plan.py",
        ".GITHUB/workflows/validate.yml",
        ".github/Workflows/validate.yml",
        "agents.md",
        "Policy/protected-paths.txt",
    ],
)
def test_every_spelling_of_a_protected_file_is_caught(spelling):
    assert first_match(spelling, PROTECTED), spelling


@pytest.mark.parametrize(
    "path",
    ["scripts-old/x.py", "sample-service/scripts/x.py", "docs/policy.md", "src/.github-notes.md", "../scripts/x.py"],
)
def test_look_alikes_and_other_folders_are_not_caught(path):
    assert first_match(path, PROTECTED) is None, path


@pytest.mark.parametrize(
    "path",
    [".", "", "./", "*", "**", "**/*.py", "*/*.py", "*.md", ".github", ".github/**", "scripts", "scripts/", "policy", "schemas", "sample-service/../scripts",
     "plans", "sample-service", "sample-service/", "sample-service/**", "sample-service/*/*.py", "sample-service/acceptance"],
)
def test_broad_scopes_that_include_a_protected_area_are_hits(path):
    assert reaches_protected(path, PROTECTED) is not None, f"{path!r} slipped through"


@pytest.mark.parametrize(
    "spelling",
    [
        "sample-service/acceptance/test_discontinue.py",
        "sample-service/acceptance/test_search.py",
        "sample-service\\acceptance\\test_search.py",
        "Sample-Service/Acceptance/test_search.py",
        "sample-service/src/../acceptance/test_search.py",
        "/workspace/sample-service/acceptance/test_search.py",
        "plans/good-plan.json",
        "plans/new-plan.json",
        "Plans/good-plan.json",
    ],
)
def test_the_definition_of_done_is_protected_not_just_described(spelling):
    """The task contract said 'the agent must not edit the acceptance tests', in prose only. The list enforces it."""
    assert first_match(spelling, PROTECTED), spelling


@pytest.mark.parametrize(
    "path",
    ["sample-service/acceptance-old/x.py", "sample-service/tests/test_discontinue.py", "plans-old/x.json", "docs/plans/x.md", "docs/tasks/task-a-discontinue.md"],
)
def test_look_alikes_of_the_new_rules_are_not_caught(path):
    assert first_match(path, PROTECTED) is None, path


@pytest.mark.parametrize(
    "path, kind",
    [
        ("scripts/validate_plan.py", "inside"),
        ("scripts/valid*.py", "inside"),
        ("scripts/sub/*.py", "inside"),
        ("AGENTS.md", "inside"),
        (".", "broad"),
        ("**", "broad"),
        (".github", "broad"),
        ("scripts", "broad"),
    ],
)
def test_the_kind_tells_a_protected_target_from_a_scope_that_merely_includes_one(path, kind):
    hit = reaches_protected(path, PROTECTED)
    assert hit is not None and hit[1] == kind, (path, hit)


@pytest.mark.parametrize(
    "path",
    [
        "sample-service/src/inventory/store.py",
        "sample-service/src/inventory/*.py",
        "sample-service/tests/",
        "sample-service/tests/test_discontinue.py",
        "sample-service/acceptance-notes.md",
        "docs/guide.md",
        "scripts-old/x.py",
        ".github/ISSUE_TEMPLATE/agent-task.yml",
        ".github/ISSUE_TEMPLATE",
        ".github/pull_request_template.md",
        ".github/*.yml",
        "docs/SANDBOX-LABS.md",
        "README.md",
        "*.txt",
        "*.py",
    ],
)
def test_ordinary_scopes_are_not_hits(path):
    assert reaches_protected(path, PROTECTED) is None, path


def test_an_exact_overlap_test_does_not_over_block_like_a_prefix_guess_would():
    # `.github/*.yml` stays in the .github folder itself (a single * never crosses a folder),
    # so it cannot reach `.github/workflows/**`. A folder-prefix check would wrongly flag it.
    assert reaches_protected(".github/*.yml", PROTECTED) is None
    assert reaches_protected(".github/**/*.yml", PROTECTED) is not None


@pytest.mark.parametrize(
    "scope, rule, expected",
    [
        ("scripts", "scripts/**", True),
        ("scripts/x.py", "scripts/**", True),
        ("scripts-old", "scripts/**", False),
        ("*", "scripts/**", True),
        ("*.txt", "scripts/**", False),
        ("**", "scripts/**", True),
        ("a*", "*b", True),               # 'ab' matches both
        ("a?", "?b", True),               # 'ab' matches both
        ("a?", "b?", False),
        ("*.py", "*.md", False),
        ("a/**", "**/b", True),           # 'a/b'
        ("a/*", "**/b", True),
        ("a/*", "b/**", False),
    ],
)
def test_globs_can_overlap_examples(scope, rule, expected):
    assert globs_can_overlap(scope, rule, scope_is_folder=False) is expected, (scope, rule)


def test_overlap_test_never_misses_a_real_overlap_property():
    """If ANY concrete path matches both globs, `globs_can_overlap` must say so.

    A miss here would be a hole in a deny rule, so this property is checked against brute force
    over many random glob pairs. (Saying 'overlap' when a bounded sample finds none is fine: a deny
    rule may over-block, but it must never under-block.)
    """
    rng = random.Random(1234)
    names = ["a", "b", "ab", "aab", "scripts", "x.py", "y.md"]
    paths = sorted({"/".join(c) for depth in (1, 2, 3) for c in itertools.product(names, repeat=depth)})
    pieces = ["a", "b", "ab", "*", "**", "?", "a*", "*b", "a?", "x.*", "*.py", "scripts"]
    globs = sorted(
        {
            "/".join(combo)
            for depth in (1, 2, 3)
            for combo in itertools.product(pieces, repeat=depth)
            if combo.count("**") <= 1
        }
    )
    globs = rng.sample(globs, 240)
    matched = {g: {p for p in paths if glob_to_regex(g).match(p)} for g in globs}
    for scope in globs:
        for rule in globs:
            if matched[scope] & matched[rule]:
                assert globs_can_overlap(scope, rule, scope_is_folder=False), f"missed overlap: {scope!r} vs {rule!r}"


def test_overlap_test_is_fast_enough_for_a_hook_with_a_short_timeout():
    # A hook that times out FAILS OPEN, so the matcher must not blow up on awkward patterns.
    nasty = "*" * 40 + "x"
    assert globs_can_overlap(nasty, "*" * 40 + "y") in (True, False)
    assert globs_can_overlap("/".join(["**"] * 12), "/".join(["a*"] * 12)) in (True, False)


@pytest.mark.parametrize(
    "destination",
    ["scripts", "scripts/new.py", "scripts/", ".github/workflows", ".github/workflows/new.yml", "policy", "AGENTS.md", "Scripts/x"],
)
def test_writing_into_a_protected_folder_or_file_is_a_hit(destination):
    assert writes_into_protected(destination, PROTECTED), destination


@pytest.mark.parametrize(
    "destination",
    [".", "", "sample-service", "sample-service/out.txt", "docs/new.md", ".github", ".github/ISSUE_TEMPLATE/x.yml", "scripts-old"],
)
def test_a_destination_elsewhere_is_not_a_hit(destination):
    assert writes_into_protected(destination, PROTECTED) is None, destination
