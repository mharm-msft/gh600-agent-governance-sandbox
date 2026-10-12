# Repository instructions for Copilot

This is a sandbox for practising the guardrails around coding agents. These instructions are **guidance**. They improve behavior but do not enforce it; the protected-paths gate, the ruleset, required checks and required reviews are the enforcement.

## Where code lives

- `sample-service/` is the application code that agent tasks change. Python 3.11+, standard library only.
- `scripts/`, `policy/`, `schemas/`, `plans/`, `rulesets/`, `.github/` are governance, and the acceptance tests in `sample-service/acceptance/` define "done". Do **not** change them. `policy/protected-paths.txt` is the authoritative list. If a task seems to need a change there, stop and say so in the pull request.

## How to work

- Run `python -m pytest` inside `sample-service` before and after every change.
- Acceptance tests in `sample-service/acceptance/` define "done" for a task. Never edit them to make them pass.
- Keep changes small and in scope. Do not add dependencies.
- Never add secrets, tokens, keys or personal data to any file. Use synthetic data only.

## Pull requests

- Fill in the **Plan** and **Evidence** sections of the pull request template. A pull request without a plan will be rejected.
- Link the workflow run that shows your tests passing.
- If the same check fails twice, stop and write down what failed, what you tried and what you suggest, then ask a person to decide.
