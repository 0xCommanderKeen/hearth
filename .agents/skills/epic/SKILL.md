---
name: epic
description: Drive a GitHub epic to pull requests, one child per isolated worktree and Codex subagent, with verification, code review and status tracking. Use for "$epic", "/epic", "work the epic", or requests to implement all slices of an existing epic. Not for creating or planning epic issues.
---

# Epic

`$epic <N>` (or a request phrased as `/epic <N>`) turns an epic issue into a chain of
PRs, one per child issue, each built by a Codex subagent in its own worktree.
Adapted from the personal Claude epic skill. You are the orchestrator: you never write code
yourself here, you read, dispatch, wait, verify and report.

## Hard rules

- **One child at a time**, in the epic's listed order, unless the user passes `--parallel`.
  Slices of an epic usually depend on each other; parallel is opt-in.
- **Never push to main, never merge.** Merging is the user's decision. Children after an
  unmerged PR are stacked on that PR's branch and their PR base is set to it.
- **Labels coordinate ownership, but are not an atomic lock.** Recheck the child and
  open PRs immediately before claiming. Claim by moving `status:ready` → `status:in-progress`
  before dispatching; move to `status:review` when its PR exists. Skip children already
  `status:in-progress` or with an open PR that says `Closes #<child>`.
- **Stop on a red slice.** If a subagent cannot get its slice green, do not start the next
  one. Report what it found; the user decides.
- Use the available Codex collaboration tools, inheriting the current model unless the
  user selects another supported model. Create actual git worktrees before dispatch;
  a spawned agent otherwise shares the checkout. Never fabricate a result; wait for its report.
- **Always show a plan first.** Before claiming or dispatching anything, print the plan
  (step 2b) and wait for confirmation unless the user already authorized that concrete
  plan or explicitly instructed proceeding without another confirmation. Installing or
  copying this skill does not authorize running an epic.

## Prerequisites and portability

Run from a Hearth checkout with Git, authenticated GitHub CLI (`gh`) and a Codex
session that exposes subagent collaboration tools. Resolve the repository from the
supplied issue URL or `gh repo view --json nameWithOwner`; use that explicit
`<owner>/<repo>` in commands below. Check the local toolchain against `README.md`
and the verification command in `AGENTS.md` before dispatching. Use a writable
worktree directory allowed by the current environment, not a path from another machine.

The prompt template is bundled beside this file. No personal skill installation is
required: its review step has a fallback when `code-review` is unavailable. Use the
collaboration API exposed by the current session, following its argument schema;
`collaboration.spawn_agent` below is an example, not a required tool namespace.

## Workflow

### 1. Read the epic

```bash
gh issue view <N> --repo <owner>/<repo> --json title,body,comments,labels,url
```

Read native GitHub sub-issues as well as checklist items in the body
(`- [ ] #k`, `- [ ] <issue URL>`):

```bash
gh api --paginate repos/<owner>/<repo>/issues/<N>/sub_issues
```

Deduplicate children by repository and number. Preserve the epic's listed order where
consistent with explicit dependencies; otherwise order blockers before dependents. If no
explicit children exist, treat body/comment references as candidates and confirm membership
and order; unrelated issue references are not children. Read any linked plan document the epic
names (for example `docs/<something>-plan.md` on a branch or on main).

For each child:

```bash
gh issue view <k> --repo <owner>/<repo> --json number,title,body,state,labels
gh pr list --repo <owner>/<repo> --search "Closes #<k>" --state open --json number,headRefName,baseRefName
```

Use the explicit repository on GitHub commands. Inspect open PR bodies/linked issues as
well as the closing-keyword search, which alone can miss existing work. Build the work
list: open children, not `status:in-progress`, without an open PR. Record skipped work
and dependency state. An in-progress prerequisite blocks its dependents; an existing
verified PR can provide their base. A closed-as-not-planned prerequisite needs a scope
decision, not an assumption that it was implemented.

### 2. Establish the base

```bash
git fetch origin
```

Use the repository's actual default branch (`main` below). The first child branches from
`origin/main`. Each later child branches from the previous
child's PR branch when that PR is still open, otherwise from `origin/main`. Record the base
you chose for each child; it goes into the subagent prompt and the PR.

### 2b. Show the plan and wait

Before touching any label or spawning any subagent, print a plan:

- the epic title and its one-line goal, plus the plan document you read, if any;
- one table row per child, in order: issue, title, what will happen (`implement`, or
  `skip` with the reason), the base branch it will branch from, and a one-line sketch of
  the work drawn from the issue body;
- the verification command the subagents will run;
- mode: sequential (default) or `--parallel`.

Unless this concrete plan is already authorized, ask the user to confirm, reorder or drop
children. Explain that this skill requires plan approval before dispatch, and link this
SKILL.md. If they change the order or drop a child, redo step 2 for the affected bases.

### 3. Claim and dispatch one child

```bash
gh issue edit <k> --repo <owner>/<repo> --remove-label "status:ready" --add-label "status:in-progress"
```

Create a unique task branch and worktree under an allowed worktree root:

```bash
git worktree add -b <task-branch> <absolute-worktree-path> <base-ref>
```

Then use `collaboration.spawn_agent` (or the equivalent tool exposed by the current
Codex environment), with a unique task name, a fresh context (`fork_turns: "none"`
when supported) and the completed [PROMPT.md](PROMPT.md). Supply the absolute worktree path, task branch, base branch/ref,
repository, issue numbers, plan document and required verification command from AGENTS.md
or applicable repository guidance. Omit model overrides unless explicitly selected.
Do not pass Claude-only `subagent_type` or `isolation` arguments to Codex tools.

The subagent must use that worktree as the working directory for every repository command.
If delegation is unavailable, report the limitation before claiming work; do not silently
substitute an implementation in the orchestrator's checkout.

Wait for completion with the collaboration wait tool, keeping the user informed. Do not
start the next dependent child while this one is running. Respect available agent slots;
reviews may need a free slot too.

### 4. Verify the subagent's claim

When the subagent reports, check it rather than trusting it:

```bash
gh pr view <PR> --repo <owner>/<repo> --json number,state,baseRefName,headRefName,body,url
gh pr checks <PR> --repo <owner>/<repo>
gh issue view <k> --repo <owner>/<repo> --json labels
```

The PR body must contain `Closes #<k>`, its base must be the base you chose, and the issue
must carry `status:review`. Fix a wrong label yourself; a wrong base or missing `Closes`
goes back to the same subagent using a follow-up task (or a message if it is still running).
Check the PR's actual commits/diff against the chosen base and its reported verification.
Pending CI is not green: wait for terminal checks before advancing; if no CI is configured,
report that explicitly and verify the required local checks.

### 5. Next child

Repeat from step 2 with the next child. If the previous PR is unmerged, its head branch is
the new base.

### 6. Report

One table: child issue, PR URL, base branch, CI status, one-line summary. Then say which
PRs must merge in which order, and anything a subagent flagged as out of scope or unsure.
Do not merge.

## Failure modes

- **Subagent stops short (no PR):** read its report, decide whether the missing piece is
  environmental (push blocked, auth) or the slice is bigger than the issue. Environmental:
  resolve routine environmental problems within authorization, otherwise report the exact
  blocker and keep the ownership label. Bigger: ensure the subagent has stopped, preserve
  its branch/worktree, release the label (`status:in-progress` → `status:ready`) and report
  the findings. Post issue comments only when authorized by the user's workflow request.
- **CI red after the PR opens:** send the subagent the failing check output with
  a follow-up task; one repair round. Still red: stop the epic and report.
- **Race at claim:** if another owner is detected, stop duplicate work and report it;
  label edits alone cannot prove exclusive ownership.
- **`--parallel`:** dispatch only dependency-independent, unclaimed children, in batches
  within available agent slots, each in its own worktree from the correct prerequisite
  base. Explain possible integration conflicts. Dependent children still wait for their
  prerequisites; parallel mode never makes missing prerequisite code available.
