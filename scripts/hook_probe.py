"""Run sample tool calls through the preToolUse guard and show which ones it denies.

Why this exists: a deny list is only as good as the spellings it understands. Cover the right-hand
column, predict each line, then run this and study the surprises.

Lines marked "known limit" are NOT caught, on purpose: the guard is a heuristic, not a sandbox, and
tests pin these gaps so nobody mistakes the hook for a wall. The defence is layers: this hook, a token
that cannot push to the default branch, required review, and the pull-request-time gates.

    python scripts/hook_probe.py        # exit 0 when every line behaves as documented, 1 otherwise
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).with_name("hook_pretooluse.py")

# (tool, value, expected, note)
#   "deny"   the guard must deny it
#   "allow"  the guard must stay silent, and should: it is harmless or read-only
#   "limit"  the guard stays silent although it should not; the note says why
PROBES: list[tuple[str, str, str, str]] = [
    ("bash", "git push", "deny", ""),
    ("bash", "git -C . push", "deny", ""),
    ("bash", "git --no-pager push", "deny", ""),
    ("bash", "sudo -u root git push", "deny", ""),
    ("bash", "timeout 5 git push", "deny", ""),
    ("bash", "sh -c 'git push'", "deny", ""),
    ("bash", "if true; then git push; fi", "deny", ""),
    ("bash", "git p''ush", "deny", ""),
    ("bash", 'echo "git push is blocked"', "allow", ""),
    ("bash", "g=git; $g push", "limit", "the command name is hidden in a variable"),
    ("bash", "python3 -c \"import subprocess; subprocess.run(['git','push'])\"", "limit", "another program does the push"),
    ("bash", "rm -rf .github", "deny", ""),
    ("bash", "mv scripts/a.py /tmp/a.py", "deny", ""),
    ("bash", "cp x.py scripts/y.py", "deny", ""),
    ("bash", "echo x >> policy/protected-paths.txt", "deny", ""),
    ("bash", "rm sample-service/acceptance/test_search.py", "deny", ""),
    ("bash", "cat scripts/labcommon.py", "allow", ""),
    ("bash", "python -m pytest", "allow", ""),
    ("bash", 'rm -rf "$(pwd)/scripts"', "limit", "the shell builds the path at run time"),
    ("bash", "tar -xf x.tar -C scripts", "limit", "another program writes the files"),
    ("edit", "sample-service/src/inventory/store.py", "allow", ""),
    ("edit", "scripts/labcommon.py", "deny", ""),
    ("edit", "scripts\\labcommon.py", "deny", ""),
    ("edit", "sample-service/../scripts/x.py", "deny", ""),
    ("edit", "Scripts/x.py", "deny", ""),
    ("edit", "/workspace/.github/workflows/validate.yml", "deny", ""),
    ("edit", "sample-service/acceptance/test_discontinue.py", "deny", ""),
]


def ask_hook(tool: str, value: str) -> str:
    """Return 'DENY' or 'no opinion' by running the real hook the way the platform would."""
    arguments = {"command": value} if tool == "bash" else {"path": value, "new_str": "x"}
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"toolName": tool, "toolArgs": arguments}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return f"HOOK ERROR (exit {result.returncode})"
    if not result.stdout.strip():
        return "no opinion"
    return "DENY" if json.loads(result.stdout).get("permissionDecision") == "deny" else "no opinion"


def render(probes=PROBES) -> tuple[list[str], int]:
    """Return the report lines and the number of lines that did not behave as documented."""
    lines, surprises = [], 0
    for tool, value, expected, note in probes:
        verdict = ask_hook(tool, value)
        wanted = "DENY" if expected == "deny" else "no opinion"
        if verdict != wanted:
            surprises += 1
        line = f"{verdict:<11} {tool:<5} {value}"
        if expected == "limit":
            line += f"   <- known limit: {note}"
        if verdict != wanted:
            line += f"   <- UNEXPECTED (wanted {wanted})"
        lines.append(line)
    return lines, surprises


def main() -> int:
    lines, surprises = render()
    print("\n".join(lines))
    denied = sum(line.startswith("DENY") for line in lines)
    limits = sum("known limit" in line for line in lines)
    print(f"\n{denied} denied, {len(lines) - denied} not denied ({limits} of them known limits), {surprises} unexpected")
    return 1 if surprises else 0


if __name__ == "__main__":
    raise SystemExit(main())
