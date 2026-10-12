# Sandbox labs

Nine exercises that switch the guardrails on and then try to get past them. Every exercise has the same parts:

- **Why**: the idea behind the control, so you can explain it and not only operate it.
- **Do**: exact commands.
- **Expect**: what should happen. Write your own prediction first. A wrong prediction is the useful part.
- **Observed**: what actually happened when this repository's owner ran it, with the date. A section that says "not recorded yet" has not been run.
- **Takeaway**: what to remember.

Nothing here touches a cloud account, a secret or a real deployment. The one deployment workflow only prints text.

## Before you start

1. You need `git`, the GitHub CLI signed in as the owner of the repository (`gh auth status`), and Python 3.12 if you want to run the tests locally.
2. Clone the repository and run the tests once: `pip install -r requirements.txt` then `python -m pytest`. They should all pass.
3. Preview the settings. This makes no call and changes nothing: `python scripts/sandbox_settings.py`
4. Apply them: `python scripts/sandbox_settings.py --apply`
5. Read them back (replace `OWNER/REPO`):

   ```
   gh api repos/OWNER/REPO/rulesets --jq ".[] | {id, name, enforcement}"
   gh api repos/OWNER/REPO/actions/permissions/workflow
   gh api repos/OWNER/REPO/environments/production --jq ".protection_rules"
   ```

If you fork this repository, change `.github/CODEOWNERS` to your own handle first, and pass `--repo YOU/REPO` to the settings script.

## The layers, in one table

| Layer | Where it lives | What it does | Can the agent switch it off? |
| --- | --- | --- | --- |
| Instructions | `.github/copilot-instructions.md`, `.github/agents/` | Tell the agent what is expected. Guidance only. | It can ignore them. |
| Hook | `examples/hooks/` (not live) | Checks a tool call before the agent runs it. | Only if it can edit the hook. |
| Pull request gates | `validate` and `Plan Gate` workflows | Report a problem on a pull request. | By editing the workflow in its own pull request (exercise 5). |
| Ruleset | `rulesets/protect-main.json` | Makes the checks and a code-owner review required, and refuses direct pushes. | No. Only a repository admin can change it. |
| Environment | `production` | Makes a person approve a deployment. | No. |

Prose is guidance. A gate reports. A ruleset or an environment is what actually blocks.

## Exercise 1: a direct push to main is refused

**Why.** Every other control assumes work arrives as a pull request. If the default branch accepts direct pushes, none of the gates matter.

**Do.**

```
git switch main
git pull
git commit --allow-empty -m "Exercise 1: try to push straight to main"
git push origin main
```

Clean up: `git reset --hard origin/main`

**Expect.** The push is rejected and the message names the rule that refused it.

**Observed:** not recorded yet.

**Takeaway.** The ruleset has no direct-push exception, not even for the admin. The only bypass is on a pull request (exercise 4), which leaves a visible record.

## Exercise 2: the Plan Gate

**Why.** An agent that opens a pull request with no plan gives the reviewer nothing to compare the diff against. The Plan Gate reads the pull request body, so a plan has to exist before a person spends time reviewing. The Learn module's example gate only checks that a template file exists in the checkout, which is true for every pull request.

**Do.**

Part A, an agent branch with an untouched template. Create `docs/scratch/ex2.md` with one line of text, then:

```
git switch -c copilot/ex2-plan
git add docs/scratch/ex2.md
git commit -m "Exercise 2: add a scratch note"
git push -u origin copilot/ex2-plan
gh pr create --base main --title "Exercise 2: Plan Gate" --body-file .github/pull_request_template.md
gh pr checks
```

Part B, fix the plan. This reruns the gate, because the workflow listens for `edited`:

```
gh pr edit --body-file exercises/pr-body-filled.md
gh pr checks
```

Part C, a human branch. Make the same change on a branch that does not start with `copilot/`:

```
git switch main
git switch -c feature/ex2-human
git cherry-pick copilot/ex2-plan
git push -u origin feature/ex2-human
gh pr create --base main --title "Exercise 2: human branch" --body "No plan on purpose"
gh pr checks
```

**Expect.** Part A: `Plan Gate` fails and the pull request cannot merge. Part B: `Plan Gate` passes, but the pull request still cannot merge because a review is also required (exercise 4). Part C: `Plan Gate` is skipped, and a skipped required check does not block.

**Observed:** not recorded yet.

**Takeaway.** A required check must always report. This workflow has no `paths:` or `branches:` filter, and the skip is a job-level `if:`. A workflow-level filter would leave the check "Expected" forever and block every pull request that does not match it. A test in `tests/test_sandbox.py` holds that line.

## Exercise 3: the protected-path gate

**Why.** An agent that can edit its own instructions, workflows or policy can quietly widen its own permissions. Prose such as "do not change these files" is guidance. The protected-path list is enforcement.

**Do.**

Part A, an agent branch that edits its own instructions as well as allowed code. Add one line at the end of `.github/copilot-instructions.md`, and one comment line in `sample-service/src/inventory/store.py`.

```
git switch main
git switch -c copilot/ex3-protected
git add -A
git commit -m "Exercise 3: widen the agent's own instructions"
git push -u origin copilot/ex3-protected
gh pr create --base main --title "Exercise 3: protected path" --body-file exercises/pr-body-filled.md
gh pr checks
gh run list --branch copilot/ex3-protected --limit 1
gh run view RUN_ID --log-failed
```

Part B, the same two edits from a human branch:

```
git switch main
git switch -c feature/ex3-human
git cherry-pick copilot/ex3-protected
git push -u origin feature/ex3-human
gh pr create --base main --title "Exercise 3: human branch" --body-file exercises/pr-body-filled.md
gh pr checks
```

**Expect.** Part A: `validate` fails at the step "Protected-path gate" and lists `.github/copilot-instructions.md` only. The allowed edit is not listed. Part B: the gate prints SKIP and `validate` passes.

**Observed:** not recorded yet.

**Takeaway.** The gate judges agent branches. A person changing the same file is judged by review, not by the gate (exercise 4).

## Exercise 4: code owners and self-approval

**Why.** Separating the person who writes a change from the person who approves it is the oldest control there is. CODEOWNERS says who must review which paths. The ruleset turns that into a requirement.

**Do.** Use the pull request from exercise 3, part B.

```
gh pr view --json reviewRequests,reviewDecision,mergeStateStatus
gh pr review --approve
gh pr merge --squash
```

Part C, the bypass. A sole maintainer cannot approve their own pull request, so the ruleset lists one bypass path: the repository admin role, in `pull_request` mode. Merge a harmless pull request that only adds a line to a file under `docs/`:

```
gh pr merge --squash --admin
```

**Expect.** Review is requested from the code owner automatically. Approving your own pull request is refused. A plain merge is refused. The `--admin` merge succeeds, and the pull request shows that the rules were bypassed.

**Observed:** not recorded yet.

**Takeaway.** The bypass is a deliberate, recorded act on a pull request. A direct push is still refused (exercise 1). With a second person, you would not need it.

## Exercise 5: a pull request can edit the rules that judge it

**Why.** For a `pull_request` event, GitHub runs the workflow files from the pull request's own merge commit. So a pull request can change the checks that are about to judge it. This was found while building this repository: the protected-path gate read its rules from the pull request's checkout, so one commit that emptied `policy/protected-paths.txt` and edited a workflow passed.

**Do.**

Part A, weaken the rule list. On a new branch `copilot/ex5-policy`, delete the `scripts/**` and `policy/**` lines from `policy/protected-paths.txt`, and add a comment line to `scripts/labcommon.py`:

```
git switch main
git switch -c copilot/ex5-policy
git add -A
git commit -m "Exercise 5A: weaken the rule list and edit what it protected"
git push -u origin copilot/ex5-policy
gh pr create --base main --title "Exercise 5A: edit the rules" --body-file exercises/pr-body-filled.md
gh pr checks
```

Part B, remove the check. On a new branch `copilot/ex5-workflow` (start it from `main` again), delete the whole step named "Protected-path gate (agent branches only)" from `.github/workflows/validate.yml`, add a comment line to `scripts/labcommon.py`, and open the pull request the same way with the title "Exercise 5B: edit the workflow".

**Expect.** Part A: `validate` fails, because the gate now reads the rules from the base branch. Part B: `validate` and `Plan Gate` pass, because the pull request's own copy of the workflow decides which steps run. The only thing stopping the merge is the code-owner review from the ruleset.

**Observed:** not recorded yet.

**Takeaway.** Part A was a real hole and is fixed and pinned by `tests/test_policy_gate.py::test_a_pull_request_cannot_weaken_the_rules_that_judge_it`. Part B is a limit of the design: a check defined inside the change cannot be the only control. The controls that hold are the required code-owner review of `.github/workflows/`, and running the sensitive part of a gate from the base branch (the Plan Gate checks out the base copy of its script). Running a whole workflow from the base branch needs `pull_request_target` or an organisation-level required workflow. This repository uses neither: `pull_request_target` runs with a privileged token and is easy to misuse.

## Exercise 6: the approval gate

**Why.** A plan file can carry an `approval` record, but the agent writes that file, so the record can be forged. Authorization has to come from something the agent cannot write. GitHub's review record is that. `scripts/approval_gate.py` asks four questions: is there an approval, from a person with write access, who is not the author, on the current commit.

**Do.**

```
python scripts/approval_gate.py --repo OWNER/REPO --pr NUMBER
python scripts/approval_gate.py --repo cli/cli --pr 14634
```

Use the pull request from exercise 5, part B for the first command. The second command shows a passing case on a public pull request.

**Expect.** The first fails and exits 1, because no one other than the author has approved. The second passes. To get a pass in your own repository you need a second account with write access.

**Observed:** not recorded yet.

**Takeaway.** This script reports. It becomes a block only if you add it as a required check, which this repository does not do, because the ruleset's required review already covers the same question.

## Exercise 7: environment approval

**Why.** A deployment is the step that changes something real. An environment with required reviewers makes the job wait for a person before any step runs, and a branch policy decides which branches may even ask.

**Do.**

Part A, the wait:

```
gh workflow run deploy-demo.yml --ref main
gh run list --workflow deploy-demo.yml --limit 1
gh api repos/OWNER/REPO/actions/runs/RUN_ID/pending_deployments
```

Approve it from the Actions page, or with the API (the environment id is in the previous output):

```
gh api --method POST repos/OWNER/REPO/actions/runs/RUN_ID/pending_deployments -F "environment_ids[]=ENV_ID" -f state=approved -f comment="Exercise 7"
```

Part B, separation of duties. Turn on `prevent_self_review`, start the workflow again and try to approve it yourself:

```
python scripts/sandbox_settings.py --apply --only environment --prevent-self-review
```

Restore the default afterwards by running the same command without the flag.

Part C, the branch policy: `gh workflow run deploy-demo.yml --ref feature/ex2-human`

**Expect.** Part A: the run waits, then runs its two `echo` steps after approval. Part B: approving your own run is refused. Part C: a branch other than `main` is not allowed to deploy.

**Observed:** not recorded yet.

**Takeaway.** Required reviewers only separate duties when the reviewer is not the person who started the run. With one person, that setting makes the deployment impossible, which is the correct result and the reason it is opt-in here.

## Exercise 8: read the settings back

**Why.** A setting you cannot read back is a setting you are guessing about. These calls show what the repository actually enforces.

**Do.**

```
gh api repos/OWNER/REPO/actions/permissions/workflow
gh api repos/OWNER/REPO/actions/permissions/fork-pr-contributor-approval
gh api repos/OWNER/REPO/rulesets --jq ".[0].id"
gh api repos/OWNER/REPO/rulesets/RULESET_ID --jq "{current_user_can_bypass, bypass_actors, rules: [.rules[].type]}"
gh api repos/OWNER/REPO/commits/main/check-runs --jq ".check_runs[] | {name, app: .app.slug}"
```

**Expect.** The workflow token is read-only and cannot approve pull requests. Outside contributors need approval before a workflow runs on their pull request. The ruleset lists the four rule types. The check-run names are the two names the ruleset requires.

**Observed:** not recorded yet.

**Takeaway.** The required name is the job name, not the workflow name. If the ruleset says `Plan Gate` and the job is called something else, every pull request waits for a check that never arrives.

## Exercise 9: a real cloud agent (guided, optional)

**Why.** Everything above uses a person pretending to be an agent. Only a real agent session shows what the platform does for you: the branch name, the pull request body, which checks wait for approval, and whether your instructions were followed.

This exercise uses Copilot premium requests, so it is yours to decide. Check first that your plan includes the cloud agent: Settings, Copilot.

**Do.**

1. Open an issue with the "Agent task" form. Use this goal: "Discontinued items cannot be sold or restocked." Set allowed scope to `sample-service/src/inventory/`.
2. Assign the issue to Copilot, or start the task with `gh agent-task create`.
3. When the draft pull request appears, record the checklist below.

**Record.**

- The branch name. Does it start with `copilot/`, so the gates apply?
- Does the pull request body contain a plan the Plan Gate accepts?
- Do the workflows run, or do they wait for "Approve and run workflows"?
- Did `copilot-setup-steps.yml` run before the agent started?
- Which files changed? Did the agent touch a protected path?
- Do the acceptance tests in `sample-service/acceptance/` pass?
- Can you merge without a second reviewer? (You should not be able to, without the bypass.)

Part B, activate the hook. Copy `examples/hooks/guardrails.hooks.example.json` to `.github/hooks/guardrails.json` in a normal pull request. That folder is protected, so an agent cannot do this for you. Run a task that asks the agent to edit `.github/workflows/validate.yml`, and look in the session log for the denial. Where a hook's output appears in the session log is not verified, so write down what you find.

**Observed:** not recorded yet.

**Takeaway.** Hooks are a layer inside the session. The pull request gates and the ruleset are the layers outside it. You want both, because each one fails in a different way.

## Known limits

These are real, and each is either pinned by a test or exercised above.

- **The gates key on a branch name.** Anyone can name a branch anything. For a person, that is by design: their change is judged by review.
- **A pull request runs its own copy of a workflow** (exercise 5, part B). Required code-owner review of `.github/` is the control.
- **The Plan Gate checks that a plan exists, not that it is true.** It does not compare the plan to the diff.
- **The hook is a heuristic.** `scripts/hook_probe.py` lists what it catches and what it knows it misses. It also reads its rules from the working tree, so a weakened rule file weakens the hook. The pull request gate reads the base branch and does not have that problem.
- **The protected-path list is a deny list.** It knows the folders it names. Other places where agent behaviour can be customised are not covered until someone adds them.
- **One maintainer means one bypass path.** That is why the ruleset bypass exists. A team would remove it.
- **Actions are referenced by tag** (`@v7`) for readability. In a real project, pin them to a full commit hash.
