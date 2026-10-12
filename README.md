# GH-600 agent governance sandbox

A small public repository for practising the guardrails around a coding agent: rulesets, required status checks, code owners, deployment approvals, and the gates in `scripts/`. All code and data are synthetic.

This is a personal study project. It is not an official GitHub or Microsoft resource, and it makes no claim about how any exam is written.

## Why it is public

Rulesets and branch protection need a public repository or a paid plan. On a private repository on a free personal account, GitHub answers: "Upgrade to GitHub Pro or make this repository public to enable this feature." This sandbox exists so those enforcement features can be switched on and tested for real.

Nothing in it is sensitive. There are no secrets, every workflow token is read-only, and the one deployment workflow only prints text.

## Start here

1. Read [docs/SANDBOX-LABS.md](docs/SANDBOX-LABS.md). It has nine exercises, each with the reason behind it.
2. Preview the settings the exercises rely on. This makes no call and changes nothing: `python scripts/sandbox_settings.py`
3. Apply them: `python scripts/sandbox_settings.py --apply`

To run the tests locally: `pip install -r requirements.txt`, then `python -m pytest`, then `python -m pytest` again inside `sample-service`.

## What is in the repository

| Path | What it is |
| --- | --- |
| `sample-service/` | A tiny inventory service that agent tasks change. Its 14 tests pass. Its 8 acceptance tests fail on purpose: they are the finish line for an agent task. |
| `.github/workflows/` | `validate` and `Plan Gate` (the two required checks), `deploy-demo` (prints text, waits for approval) and `copilot-setup-steps`. |
| `.github/CODEOWNERS`, `policy/protected-paths.txt` | Who must review what, and which paths an agent branch may not change. |
| `rulesets/protect-main.json` | The ruleset for the default branch. |
| `scripts/` | The gates (`policy_gate.py`, `plan_gate.py`, `approval_gate.py`), a hook and its probe, a linter for agent customization files, and `sandbox_settings.py`. |
| `exercises/` | Pull request bodies the exercises use. |
| `examples/hooks/` | A hook configuration that is not live. Exercise 9 activates it on purpose. |
| `tests/` | Tests for all of the above, including checks that the ruleset, the workflows and CODEOWNERS agree with each other. |

## How the layers fit

| Layer | What it does | Blocks a merge? |
| --- | --- | --- |
| Instructions in `.github/` | Tell the agent what is expected. | No. Guidance only. |
| Hook | Checks a tool call before the agent runs it. | Not a merge. It blocks a tool call, and only while it is active. |
| `validate` and `Plan Gate` | Report a problem on a pull request. | Only because the ruleset requires them. |
| Ruleset | Requires those checks, a code-owner review and a pull request. Refuses direct pushes. | Yes. |
| `production` environment | Makes a person approve a deployment. | It blocks the deployment job. |

Prose is guidance. A gate reports. A ruleset or an environment is what blocks.

## What this repository does not do

- It does not touch a cloud account, a secret or a real deployment.
- `sandbox_settings.py` never changes anything unless you pass `--apply`.
- The hook is not live. It is a file in `examples/` until you activate it in a reviewed pull request.

## Known limits

Listed with their reasons at the end of [docs/SANDBOX-LABS.md](docs/SANDBOX-LABS.md). The main one: a pull request runs its own copy of a workflow, so a check defined inside the change cannot be the only control.

## License

MIT. See [LICENSE](LICENSE).
