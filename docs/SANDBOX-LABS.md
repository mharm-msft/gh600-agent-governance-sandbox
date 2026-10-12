# Sandbox labs

Nine exercises that switch the guardrails on and then try to get past them. Every exercise has the same parts:

- **Why**: the idea behind the control, so you can explain it and not only operate it.
- **Do**: exact commands.
- **Expect**: what should happen. Write your own prediction first. A wrong prediction is the useful part.
- **Observed**: what actually happened when this repository's owner ran it on 2026-10-12. Quotes are copied from the real output.
- **Takeaway**: what to remember.

Exercises 1 to 8 were run for real. Exercise 9 needs a Copilot plan and premium requests, so it has not been run.

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
   gh api repos/OWNER/REPO/environments/production --jq ".protection_rules[] | select(.type==\"required_reviewers\") | {prevent_self_review, reviewers: [.reviewers[].reviewer.login]}"
   ```

A read straight after a write can return the old value. After applying the settings, `delete_branch_on_merge` read back as `false` a few seconds after the call, and as `true` a little later. If a setting looks wrong, read it again before you change it.

If you fork this repository, change `.github/CODEOWNERS` to your own handle first, and pass `--repo YOU/REPO` to the settings script.

## The layers, in one table

| Layer | Where it lives | What it does | Can the agent switch it off? |
| --- | --- | --- | --- |
| Instructions | `.github/copilot-instructions.md`, `.github/agents/` | Tell the agent what is expected. Guidance only. | It can ignore them. |
| Hook | `examples/hooks/` (not live) | Checks a tool call before the agent runs it. | Only if it can edit the hook. |
| Pull request gates | `validate` and `Plan Gate` workflows | Report a problem on a pull request. | Yes, by editing the workflow in its own pull request (exercise 5). |
| Ruleset | `rulesets/protect-main.json` | Makes the checks and a code-owner review required, and refuses direct pushes. | Not by editing files. The repository admin role can bypass it on a pull request (exercise 4). |
| Environment | `production` | Makes a person approve a deployment. | No. |

Prose is guidance. A gate reports. A ruleset or an environment is what actually blocks.

## What running these found

Ten things the exercises turned up. Some corrected the first draft of this document, and one corrected a claim in the code.

1. **The protected-path gate read its rules from the pull request.** One commit that emptied `policy/protected-paths.txt` and edited a workflow passed the gate. This was found locally while building the repository and fixed before the first push, so it was never seen failing in CI. Four tests pin the fix (exercise 5, part A).
2. **A failing test step hid the gate's verdict.** On the first run of exercise 5, part A, the script tests failed, the gate step was skipped, and a skipped gate looks the same as a missing one. Found in CI and fixed in PR #7 with `!cancelled()` (exercise 5, part A).
3. **A pull request can remove the gate and pass every check.** It deleted the gate step, edited a protected script and deleted the test that pinned the step. Only the required review held it (exercise 5, part B).
4. **The admin bypass overrides a failing required check, not only the missing review.** It is all-or-nothing for the admin role (exercise 4, part D).
5. **A skipped required check does not block a merge.** The Plan Gate design relies on that, and it is now observed (exercise 2).
6. **With `prevent_self_review` on, a sole reviewer sees no pending request at all.** The API answers "No pending deployment requests to approve or reject", not "you cannot approve your own run" (exercise 7, part B).
7. **GitHub adds a rule to the ruleset on its own.** `require_extra_approval_for_unattributed_changes` is on by default (public preview). When Copilot opens a pull request that is not attributed to a person, one more approval than you configured is required.
8. **The required check name is the job name.** `Plan Gate` does not appear on `main`, because it only runs on pull requests (exercise 8).
9. **`[skip ci]` does not get an agent past the gate.** No workflow starts, and required checks that never start block the merge (exercise 2, part D).
10. **A passing check cannot mask a failing check with the same name**, in either order (exercise 2, part E). The first draft claimed the opposite, as a reason for the push trigger in `validate.yml`. That rationale had never been tested and was wrong, so the comment and test docstring were corrected.

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

**Observed.** The push was made as the repository owner and admin, and it was still rejected:

```
remote: error: GH013: Repository rule violations found for refs/heads/main.
remote: Review all repository rules at https://github.com/mharm-msft/gh600-agent-governance-sandbox/rules?ref=refs%2Fheads%2Fmain
remote:
remote: - Changes must be made through a pull request.
remote:
remote: - 2 of 2 required status checks are expected.
remote:
To https://github.com/mharm-msft/gh600-agent-governance-sandbox.git
 ! [remote rejected] main -> main (push declined due to repository rule violations)
error: failed to push some refs to 'https://github.com/mharm-msft/gh600-agent-governance-sandbox.git'
```

**Takeaway.** The ruleset has no direct-push exception, not even for the admin. The only bypass is on a pull request (exercise 4), and it leaves a record.

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

Part D, try to skip the checks. An agent that wants no gate to run might put `[skip ci]` in its commit message. Create `docs/scratch/skip.md` with one line of text, then:

```
git switch main
git switch -c copilot/ex2-skip-ci
git add docs/scratch/skip.md
git commit -m "Exercise 2D: try to skip the checks [skip ci]"
git push -u origin copilot/ex2-skip-ci
gh pr create --base main --title "Exercise 2D: skip the checks" --body-file exercises/pr-body-filled.md
gh pr checks
gh run list --branch copilot/ex2-skip-ci
```

Part E, two checks with one name. This one needs a temporary ruleset, so it is optional. On a throwaway base branch, create a temporary ruleset that requires only `validate` and `Plan Gate` (no review). Then open a pull request into that branch from a branch that does three things in two commits: removes `branches: [main]` under `push:` in `.github/workflows/validate.yml`, adds a comment line to `scripts/labcommon.py` (a protected path, so the pull request run fails at the gate), and deletes the test `test_a_push_to_a_feature_branch_does_not_start_a_competing_validate_run` from `tests/test_sandbox.py` (so the push run passes, because the gate step is skipped on pushes). The commit that deletes the test is the second one. Read the merge state, then re-run the passing push run with `gh run rerun RUN_ID` so that it becomes the newest `validate`, and read the merge state again. Delete the temporary ruleset and branch afterwards.

**Expect.** Part A: `Plan Gate` fails and the pull request cannot merge. Part B: `Plan Gate` passes, but the pull request still cannot merge because a review is also required (exercise 4). Part C: `Plan Gate` is skipped, and a skipped required check does not block. Part D: no workflow starts, and the merge is blocked. Part E: a failing check blocks the merge even when a passing check with the same name is newer.

**Observed.**

Part A. `Plan Gate` failed, `validate` passed, and the merge state was `BLOCKED`. The gate's own message, from the failed run:

```
FAIL plan gate
  - plan field 'Goal' is missing or empty
  - plan field 'Scope' is missing or empty
  - plan field 'Steps' is missing or empty
  - plan field 'Success criteria' only repeats the pull request template; write your own
  - plan field 'Risks' is missing or empty
  - plan field 'Rollback' is missing or empty
  - '## Evidence' needs at least one link or run reference
```

Part B. Editing the body started a new `Plan Gate` run, which passed. `validate` did not run again, because its default triggers do not include `edited`. The failed first run stays in the run list. The merge state was still `BLOCKED`, and a plain merge was refused:

```
X Pull request mharm-msft/gh600-agent-governance-sandbox#1 is not mergeable: the base branch policy prohibits the merge.
To have the pull request merged after all the requirements have been met, add the `--auto` flag.
To use administrator privileges to immediately merge the pull request, add the `--admin` flag.
```

Part C. `gh pr checks` showed `Plan Gate  skipping`, and the check-run API reported `conclusion=skipped`. The merge state was `BLOCKED`, but the review rule alone explains that. To see whether the skipped check blocks by itself, a temporary ruleset required only `validate` and `Plan Gate` on a throwaway branch, and the same pull request was opened against that branch. The merge state was `CLEAN`. A skipped required check does not block. The temporary ruleset and branch were deleted afterwards.

Part D. Putting `[skip ci]` in the head commit's message meant GitHub created no workflow runs at all. `gh pr checks` printed `no checks reported on the 'copilot/ex2-skip-ci' branch`, and `gh run list` had nothing for the branch. The merge state was `BLOCKED`. Again the review rule could explain that, so the same temporary-ruleset test was repeated with no review rule, and the merge state was still `BLOCKED`: `X Pull request ...#12 is not mergeable: the base branch policy prohibits the merge.` Required checks that never start block the merge, so `[skip ci]` does not get an agent past the gate. It makes the pull request unmergeable.

The same thing then happened by accident. The commit message of the pull request that recorded this result mentioned the token in a sentence about the feature, GitHub honored it, and the pull request got no checks at all until the message was rewritten. The token works anywhere in the head commit's message, not only as a bare directive.

Part E. In the first commit both runs failed, because the repository's own test pinned the push trigger, so it told nothing. In the second commit the push run passed (the gate step does not run on pushes) and the pull request run failed at the gate, on the same commit, under the same check name:

```
name=[validate] conclusion=failure started=2026-10-12T14:57:20Z
name=[validate] conclusion=success started=2026-10-12T14:57:17Z
```

The merge state was `BLOCKED`. After re-running the passing push run, so that it was the newest `validate` (started 14:58:05 against 14:57:20), the merge state was still `BLOCKED`. In this configuration, a failing check blocks the merge in both orders, and a passing push run does not mask a failing pull request run. The first draft of this repository said the opposite risk was real, in a workflow comment and a test docstring. That was never tested, and it has been corrected.

**Takeaway.** A required check must always report. This workflow has no `paths:` or `branches:` filter, and the skip is a job-level `if:`. A workflow-level filter would leave the check "Expected" forever and block every pull request that does not match it. GitHub documents the same for a commit message such as `[skip ci]` (observed in part D). A test in `tests/test_sandbox.py` holds the filter line. `validate` also runs only on pull requests and on pushes to `main`. That avoids two checks with one name and halves the runs. It is hygiene and not a safety control (part E).

## Exercise 3: the protected-path gate

**Why.** An agent that can edit its own instructions, workflows or policy can quietly widen its own permissions. Prose such as "do not change these files" is guidance. The protected-path list is enforcement.

**Do.**

Part A, an agent branch that edits its own instructions as well as allowed code. Add one line at the end of `.github/copilot-instructions.md`, and one comment line at the end of `sample-service/src/inventory/store.py`.

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

**Observed.**

Part A. `validate` failed and `Plan Gate` passed. The gate's output, which matches what `python scripts/policy_gate.py` prints locally:

```
FAIL agent branch 'copilot/ex3-protected' changes protected paths:
  - .github/copilot-instructions.md  (rule: .github/copilot-instructions.md)
A human must make these changes from a normal branch.
```

The edit to `store.py` is not listed.

Part B. `validate` passed and `Plan Gate` was skipped. The gate step printed:

```
SKIP 'feature/ex3-human' is not an agent branch (copilot/*); gate not applicable
```

The same log also shows eight `FAILED acceptance/...` lines. The acceptance tests fail by design, and that step reports without failing the job.

**Takeaway.** The gate judges agent branches. A person changing the same file is judged by review, not by the gate (exercise 4).

## Exercise 4: code owners and self-approval

**Why.** Separating the person who writes a change from the person who approves it is the oldest control there is. CODEOWNERS says who must review which paths. The ruleset turns that into a requirement.

**Do.** Use the pull request from exercise 3, part B. It changes a file under `.github/`, so a code owner must approve it.

```
gh pr view --json reviewRequests,reviewDecision,mergeStateStatus
gh pr review --approve
gh pr merge --squash
```

Part C, the bypass. A sole maintainer cannot approve their own pull request, so the ruleset lists one bypass path: the repository admin role, in `pull_request` mode. Merge a pull request whose checks are green:

```
gh pr merge --squash --admin
```

Then read the record it left:

```
gh api "repos/OWNER/REPO/rulesets/rule-suites?time_period=day" --jq ".[] | {pushed_at, ref, actor_name, result}"
```

Part D, how strong is the bypass? Create a temporary ruleset that is a copy of `rulesets/protect-main.json`, with `name` changed and `conditions.ref_name.include` set to a throwaway branch. Open a pull request into that branch with a deliberately failing test, so `validate` fails and no review exists, then run `gh pr merge --squash --admin`. Delete the temporary ruleset and branch afterwards.

**Expect.** Approving your own pull request is refused. A plain merge is refused. The `--admin` merge succeeds and shows up in the rule-suite record as a bypass.

**Observed.**

In this repository the author is the only code owner, so no review was requested from anyone: `reviewRequests` was empty and `reviewDecision` was `REVIEW_REQUIRED`. This corrects the first draft of this document, which said the review would be requested automatically.

```
failed to create review: GraphQL: Review Can not approve your own pull request (addPullRequestReview)
```

```
X Pull request mharm-msft/gh600-agent-governance-sandbox#5 is not mergeable: the base branch policy prohibits the merge.
To have the pull request merged after all the requirements have been met, add the `--auto` flag.
To use administrator privileges to immediately merge the pull request, add the `--admin` flag.
```

Part C. PR #7 (a fix found in exercise 5) had green checks and was blocked on review. `gh pr merge 7 --squash --admin` exited 0 and printed nothing. The pull request was merged as `8e12f60`. The rule-suite record:

```
{"actor_name":"mharm-msft","pushed_at":"2026-10-12T14:29:15Z","ref":"refs/heads/main","result":"bypass"}
{"actor_name":"mharm-msft","pushed_at":"2026-10-12T14:19:25Z","ref":"refs/heads/main","result":"fail"}
```

The second line is the direct push from exercise 1. `viewerCanMergeAsAdmin` was `false` in the GraphQL answer even though the merge worked, because that field describes branch protection and not ruleset bypass.

Part D. The pull request had a failing `validate`, a passing `Plan Gate` and no review. `gh pr merge --squash --admin` exited 0 and the pull request merged. The record said `result=bypass`.

**Takeaway.** The bypass is a recorded act on a pull request, and a direct push is still refused (exercise 1). It is also all-or-nothing. It overrides a failing required check as well as the missing review, and the record does not say which rules were overridden. In a solo repository that is the price of being able to merge at all. With a second person you would remove the bypass entry.

## Exercise 5: a pull request can edit the rules that judge it

**Why.** For a `pull_request` event, GitHub runs the workflow files from the pull request's own merge commit. So a pull request can change the checks that are about to judge it. Two different holes follow from that, and the exercise tries both.

**Do.**

Part A, weaken the rule list. On a new branch `copilot/ex5-policy`, delete the `scripts/**` and `policy/**` lines from `policy/protected-paths.txt`, and add a comment line at the end of `scripts/labcommon.py`:

```
git switch main
git switch -c copilot/ex5-policy
git add -A
git commit -m "Exercise 5A: weaken the rule list and edit what it protected"
git push -u origin copilot/ex5-policy
gh pr create --base main --title "Exercise 5A: edit the rules" --body-file exercises/pr-body-filled.md
gh pr checks
```

Part B, remove the check. On a new branch `copilot/ex5-workflow` (start it from `main` again), delete the whole step named "Protected-path gate (agent branches only)" from `.github/workflows/validate.yml`, add a comment line to `scripts/labcommon.py`, commit, push, and open a pull request with the title "Exercise 5B: edit the workflow". Read the checks. Then make a second commit on the same branch that deletes the test function `test_the_protected_path_gate_reports_even_when_an_earlier_step_fails` from `tests/test_workflows.py`, push it, and read the checks again.

**Expect.** Part A: the gate fails, because it reads the rules from the base branch and not from the pull request. Part B, first commit: the repository's own test that pins the gate step fails. Part B, second commit: every check passes, and the only thing stopping the merge is the review.

**Observed.**

Part A, first run. `validate` failed at step 5, "Script tests", because the repository's own tests pin the policy file. Steps 6 to 9 were **skipped**, and step 9 is the gate. The pull request therefore never showed the gate's verdict. That is finding 2 above. After the fix in PR #7 (`!cancelled()` on the gate step), the pull request was closed and reopened so that it ran again.

Part A, second run. The script tests still failed, and the gate now ran and failed. It judged the change by the rules on `main`, although the pull request had deleted two rules from its own copy:

```
FAIL agent branch 'copilot/ex5-policy' changes protected paths:
  - policy/protected-paths.txt  (rule: policy/**)
  - scripts/labcommon.py  (rule: scripts/**)
A human must make these changes from a normal branch.
```

What this run did not show is the original defect. That was found locally before the first push, where the same change printed `PASS agent branch 'copilot/self-edit' changed 2 file(s); none are protected`, and it is pinned by tests that fail if the gate goes back to reading the pull request's rules.

Part B, first commit. `validate` failed at "Script tests", with no gate step in the job at all. The failing test was the one added by PR #7:

```
FAILED tests/test_workflows.py::test_the_protected_path_gate_reports_even_when_an_earlier_step_fails - StopIteration
```

The first draft of this document predicted that `validate` would pass here. That was wrong, and what stopped it was an accident: a test that happens to pin the step. Nothing reported that a workflow and a protected script had been edited.

Part B, second commit. After the test was deleted in the same pull request, `validate` passed, `Plan Gate` passed, and steps 4 to 9 all succeeded. There was no gate step to run. The pull request changed `.github/workflows/validate.yml` and `scripts/labcommon.py`, both protected paths, and no check said so. The merge state was `BLOCKED` with `reviewDecision` `REVIEW_REQUIRED`, and a plain merge was refused with the same "base branch policy prohibits the merge" message as in exercise 2.

**Takeaway.** Part A was a real hole, and it is fixed and pinned by `tests/test_policy_gate.py::test_a_pull_request_cannot_weaken_the_rules_that_judge_it`. Part B is a limit of the design, not a bug to patch: every check that runs inside a pull request can be removed by that pull request. The control that held was the required review. A test that pins a control only helps if the pull request cannot also delete the test, and the pull request can.

What would close part B without a reviewer is a check that runs from the base branch, so the pull request cannot edit it. That means `pull_request_target`, or an organisation-level required workflow. This repository uses neither. A `pull_request_target` workflow runs with a privileged token and is easy to misuse, so it should read only file names through the API and execute nothing from the pull request. That is a possible next exercise, and it has not been built.

## Exercise 6: the approval gate

**Why.** A plan file can carry an `approval` record, but the agent writes that file, so the record can be forged. Authorization has to come from something the agent cannot write. GitHub's review record is that. `scripts/approval_gate.py` asks four questions: is there an approval, from a person with write access, who is not the author, on the current commit.

**Do.**

```
python scripts/approval_gate.py --repo OWNER/REPO --pr NUMBER
python scripts/approval_gate.py --repo cli/cli --pr 14634
```

Use the pull request from exercise 5, part B for the first command. The second command shows a passing case on a public pull request.

**Expect.** The first fails and exits 1, because no one other than the author has approved. The second passes. To get a pass in your own repository you need a second account with write access.

**Observed.**

```
FAIL approval gate
  - no human has approved this pull request yet
```

Exit code 1, for PR #8 in this repository. For the public pull request, exit code 0:

```
PASS approval gate
  - approved on 424f38d by @williammartin
```

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

Approve it from the Actions page, or with the API (the environment id is in the previous output), then read the approval record:

```
gh api --method POST repos/OWNER/REPO/actions/runs/RUN_ID/pending_deployments -F "environment_ids[]=ENV_ID" -f state=approved -f comment="Exercise 7"
gh api repos/OWNER/REPO/actions/runs/RUN_ID/approvals --jq ".[] | {state, comment, user: .user.login}"
```

Part B, separation of duties. Turn on `prevent_self_review`, start the workflow again and try to approve it yourself:

```
python scripts/sandbox_settings.py --apply --only environment --prevent-self-review
```

Cancel the waiting run with `gh run cancel RUN_ID`, and restore the default by running the same command without the flag.

Part C, the branch policy:

```
gh workflow run deploy-demo.yml --ref feature/ex2-human
gh api repos/OWNER/REPO/actions/runs/RUN_ID/jobs --jq ".jobs[0].id"
gh api repos/OWNER/REPO/check-runs/JOB_ID/annotations --jq ".[].message"
```

**Expect.** Part A: the run waits, then runs its two `echo` steps after approval. Part B: approving your own run is refused. Part C: a branch other than `main` is not allowed to deploy.

**Observed.**

Part A. The run started with status `waiting`, and the job listed no steps, so nothing had executed. `pending_deployments` showed `"current_user_can_approve":true`. After approval the run completed with `success`, and the log printed the two lines:

```
A required reviewer approved this run, so it is executing.
This job only prints text. It changes nothing.
```

The approval record was `{"comment":"Exercise 7","state":"approved","user":"mharm-msft"}`.

Part B. The settings script reported `OK [environment] PUT ...` and then `SKIP [environment] already present: ...deployment-branch-policies`, so running it twice does not duplicate the branch policy. With `prevent_self_review` on, `pending_deployments` showed `"current_user_can_approve":false`. The attempt to approve returned HTTP 422:

```
{"message":"Validation Failed","errors":"No pending deployment requests to approve or reject", ... "status":"422"}
```

GitHub does not say that you cannot approve your own run. To the only reviewer, the pending request is invisible. The run stayed `waiting` until it was cancelled.

Part C. The run did not wait. It failed straight away, the job had no steps, and the annotations said:

```
Branch "feature/ex2-human" is not allowed to deploy to production due to environment protection rules.
The deployment was rejected or didn't satisfy other protection rules.
```

**Takeaway.** Required reviewers only separate duties when the reviewer is not the person who started the run. With one person, that setting makes the deployment impossible, and the error does not explain why. That is the reason it is opt-in here.

## Exercise 8: read the settings back

**Why.** A setting you cannot read back is a setting you are guessing about. These calls show what the repository actually enforces.

**Do.**

```
gh api repos/OWNER/REPO/actions/permissions/workflow
gh api repos/OWNER/REPO/actions/permissions/fork-pr-contributor-approval
gh api repos/OWNER/REPO/rulesets --jq ".[0].id"
gh api repos/OWNER/REPO/rulesets/RULESET_ID --jq "{current_user_can_bypass, bypass_actors, rules: [.rules[].type]}"
gh pr view NUMBER --json headRefOid --jq .headRefOid
gh api repos/OWNER/REPO/commits/SHA/check-runs --jq ".check_runs[] | {name, conclusion, app: .app.slug}"
```

**Expect.** The workflow token is read-only and cannot approve pull requests. Outside contributors need approval before a workflow runs on their pull request. The ruleset lists the four rule types. The check-run names on a pull request are the two names the ruleset requires.

**Observed.**

```
{"default_workflow_permissions":"read","can_approve_pull_request_reviews":false}
{"approval_policy":"all_external_contributors"}
{"bypass_actors":[{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"pull_request"}],"current_user_can_bypass":"pull_requests_only","rules":["deletion","non_fast_forward","pull_request","required_status_checks"]}
{"app":"github-actions","conclusion":"success","name":"validate"}
{"app":"github-actions","conclusion":"success","name":"Plan Gate"}
```

The first draft of this exercise read the check runs on `main`. Those showed `deploy` twice and `validate`, and no `Plan Gate`, because that job only runs on pull requests. The two required names show up on a pull request's head commit.

Reading the ruleset back also shows a field this repository did not set. GitHub added `require_extra_approval_for_unattributed_changes: true` to the pull request rule by default (public preview). If Copilot opens a pull request that is not attributed to a person, the ruleset requires one more approval than the number configured. With one required approval, that is two.

**Takeaway.** The required name is the job name, not the workflow name. If the ruleset says `Plan Gate` and the job is called something else, every pull request waits for a check that never arrives. Required checks are also pinned to the GitHub Actions app (`integration_id` 15368), so another app cannot satisfy them by posting a status with the same name.

## Exercise 9: a real cloud agent (guided, optional)

**Why.** Everything above uses a person pretending to be an agent. Only a real agent session shows what the platform does for you: the branch name, the pull request body, which checks wait for approval, and whether your instructions were followed.

This exercise uses Copilot premium requests, so it is yours to decide. Check first that your plan includes the cloud agent: Settings, Copilot. It has not been run.

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
- How many approvals does the ruleset ask for on this pull request? Check whether the extra approval for unattributed Copilot pull requests applies.
- Can you merge without a second reviewer? (You should not be able to, without the bypass.)

Part B, activate the hook. Copy `examples/hooks/guardrails.hooks.example.json` to `.github/hooks/guardrails.json` in a normal pull request. That folder is protected, so an agent cannot do this for you. Run a task that asks the agent to edit `.github/workflows/validate.yml`, and look in the session log for the denial. Where a hook's output appears in the session log is not verified, so write down what you find.

**Observed:** not run.

**Takeaway.** Hooks are a layer inside the session. The pull request gates and the ruleset are the layers outside it. You want both, because each one fails in a different way.

## Known limits

These are real, and each is either pinned by a test or observed above.

- **Every check that runs inside a pull request can be removed by that pull request** (exercise 5, part B). Required code-owner review is the control that held. A check that runs from the base branch would close the gap, and this repository does not have one.
- **The gates key on a branch name.** Anyone can name a branch anything. For a person, that is by design: their change is judged by review.
- **The admin bypass is all-or-nothing** (exercise 4, part D). It overrides failing checks as well as the review, and the record says `bypass` without naming the rules. One maintainer means one bypass path, and a team would remove it.
- **A gate that does not run looks like a gate that does not exist** (exercise 5, part A). The gate step now runs after failing steps. A skipped required check passes (exercise 2).
- **The Plan Gate checks that a plan exists, not that it is true.** It does not compare the plan to the diff.
- **The hook is a heuristic.** `scripts/hook_probe.py` lists what it catches and what it knows it misses. It also reads its rules from the working tree, so a weakened rule file weakens the hook. The pull request gate reads the base branch and does not have that problem.
- **The protected-path list is a deny list.** It knows the folders it names, including `.github/hooks/` and `rulesets/`. Other places where agent behaviour can be customised are not covered until someone adds them.
- **`prevent_self_review` makes a one-person deployment impossible** and the error does not say why (exercise 7, part B).
- **A read straight after a write can be stale** (see "Before you start").
- **Actions are referenced by tag** (`@v7`) for readability. In a real project, pin them to a full commit hash.
