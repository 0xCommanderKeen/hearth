# Subagent prompt template

Fill every `{…}` before spawning. Keep the whole prompt; the subagent has no other context.

```
You are implementing one slice of an epic in the repository {repo} (GitHub {owner/repo}).
Your isolated git worktree is {worktree_path}; task branch: {task_branch}.
Use this absolute path as the working directory for every repository command. Work only here.

Slice: issue #{k}. Epic: issue #{epic}. Base branch: {base}.
Plan document, if any: {plan_path} (read it first; it records decisions already made).

## Setup
1. The orchestrator already created your worktree and branch. Verify the checkout path,
   current branch and base `{base_ref}` before editing; do not create a second branch or
   reset existing work. Fetch the remote if needed without changing another checkout.
2. Read the repo's CLAUDE.md / AGENTS.md and follow them. Read the full issue with
   `gh issue view {k} --repo {owner/repo} --json title,body,comments` and the epic body for context.
3. Read the relevant memory files if the repo's CLAUDE.md names a memory protocol.

## Implement
Implement the work described by the issue. Use test-driven development where possible, at
pre-agreed seams: write the failing test, make it pass, refactor. Run typechecking
regularly, single test files regularly, and the full verification once at the end:
`{verify_command}`. Do not stop at a green subset; the full command must pass.
If the issue says a schema changes, ship the migration with it, per the repo's rules.
Verify the branch immediately before every commit; commit only on `{task_branch}`.
Use an attribution trailer only if the environment requires one.

## Review
When the work is complete and green, run a code review of your diff against `{base}`
(the /code-review skill if available, otherwise a careful self-review for correctness
and for the issue's acceptance criteria) and fix what it finds.

## Ship
1. Push the branch: `git push -u origin <branch>`.
2. Open the PR against `{base}` with `gh pr create --repo {owner/repo} --base {base}`. The body must:
   - start with why the change exists, in a short paragraph,
   - list what changed,
   - contain the line `Closes #{k}`,
   - say it is slice {k} of epic #{epic},
   - include the verification performed and any required PR attribution footer.
   Use a temporary body file and `--body-file` for multiline PR text.
3. Move the issue label: `gh issue edit {k} --repo {owner/repo} --remove-label "status:in-progress" --add-label "status:review"`.
4. Report the PR URL to the orchestrator. Add an issue comment only if the user's workflow
   authorization or repository instructions call for one.

## Rules
- Never push to main. Never merge. Never force-push.
- Do not widen scope beyond the issue; if you find adjacent work, note it in the PR body
  under "Out of scope" instead of doing it.
- If you cannot get the full verification green, do not open a PR. Report exactly what
  fails, with the output, and what you think the cause is.
- If the repo's memory protocol asks for a journal entry, follow it within permitted paths
  and coordinate shared-file writes with the orchestrator.

## Report back
Reply with: the PR URL (or "no PR" and why), worktree path, head commit, base branch, the verification command
and its result line, anything you left out of scope, and anything you are unsure about.
```
