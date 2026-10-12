## Plan (required)
- **Goal:** Add a one-line note file so the Plan Gate has a real plan to read.
- **Scope (paths/files):** docs/scratch/ex2.md only
- **Steps:**
  1. Create docs/scratch/ex2.md with one line of text.
  2. Open this pull request from the agent branch.
  3. Wait for the checks to finish.
- **Success criteria (verifiable):**
  - [ ] The Plan Gate check passes
  - [ ] Only docs/scratch/ex2.md changed
- **Risks + mitigations:** None. A text file under docs/ cannot affect the build.
- **Rollback / escalation plan:** Revert the pull request. If a check fails twice, stop and ask a person.

## Evidence
- Workflow run(s): https://github.com/mharm-msft/gh600-agent-governance-sandbox/actions
- Scan results (if applicable): none for this change
