from hook_probe import PROBES, render


def test_every_probe_behaves_as_documented():
    lines, surprises = render()
    assert surprises == 0, "\n".join(lines)


def test_the_probe_list_mixes_denials_harmless_lookalikes_and_known_limits():
    assert {expected for _, _, expected, _ in PROBES} == {"deny", "allow", "limit"}
    assert all(note for _, _, expected, note in PROBES if expected == "limit"), "every known limit needs a reason"


def test_both_kinds_of_tool_call_are_probed():
    assert {tool for tool, *_ in PROBES} == {"bash", "edit"}


def test_the_acceptance_tests_are_probed_so_the_definition_of_done_stays_protected():
    values = {value for _, value, expected, _ in PROBES if expected == "deny"}
    assert "sample-service/acceptance/test_discontinue.py" in values
    assert "rm sample-service/acceptance/test_search.py" in values
