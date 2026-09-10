# Resident page design review

2026-09-10 · Layout A selected by the operator and implemented on the live resident page.

## Findings

- Identity and availability compete with long technical IDs, revision numbers, and
  login details. The first screen should answer who this resident is, what it does,
  whether it can work, and what needs attention.
- An unbounded vertical stack mixes task creation, results, activity, configuration,
  memory, journal, correspondence, provenance, and management authority. Separate
  overview from detailed sections, keeping one clear New task action in the header.
- The full activity log appears before the task controls. Keep a short recent list
  on Overview and give the full timeline its own tab.
- “Run summary”, “Read summary”, and “A read-only assignment” are inaccurate for a
  management resident. The current live page now uses Run task, View result, Task
  instructions, and Task result, and describes configured tools and permissions.
- Cancellation intent, observed termination, and settlement need distinct language.
  A blocked overview should state the reason and link to the relevant activity.
- Technical IDs and configuration revisions belong in Settings or task details.
  Estimated usage must remain clearly distinguished from subscription billing.

## Compare the studies

The Docker development frontend serves:

- `/resident-designs/index.html?variant=overview`: A, Overview. Horizontal tabs,
  current state and usage, recent tasks, then context and activity. Recommended
  starting point for regular operation.
- `/resident-designs/index.html?variant=workspace`: B, Workspace. A compact global
  rail and persistent resident section navigation; task and activity information
  receive more prominence than purpose. Tabs become horizontal on phones.
- `/resident-designs/index.html?variant=profile`: C, Profile. A quieter, wider
  profile with serif headings, restrained panels, and purpose before task history.

Each study has Overview, Tasks, Activity, Memory, Skills, Access, and Settings.
All use the same fictional sample records. Task results, task filters, memory
history, keyboard tabs, task and settings previews, pause/resume, and a state
selector work locally without API requests. Task and settings forms explicitly
explain that nothing is submitted. Query parameters preserve the chosen variant
and tab for sharing. Real navigation links return to Hearth.

## Live integration

Use live snapshot fields for presence, known usage, and actual task counts; retain
unknown/stale distinctions and never infer idle from a missing event. Map memory
and journal into Memory, provenance and instructions into Skills, grants and
mounted inputs into Access, and runtime/lifecycle controls into Settings. Keep
correspondence and scheduled work discoverable in Tasks, with their existing full
editors reachable from there. Preserve run, journal, and correspondence deep links
by selecting the owning tab. Retain unsent drafts across tab switches, and retain
restore/read-only gates and pending-command identity across store changes.

The live page now uses A: Overview, Tasks, Activity, Memory, Skills, Access, and
Settings. The task form, configuration editor, and correspondence remain mounted
when hidden so unsent drafts and pending command identities survive tab switches.
The full activity history retains pagination; Overview shows only four events.
Existing journal, correspondence, work, and run links select the appropriate view.
Keyboard arrows, Home, and End navigate the tabs.

The overview labels snapshot counts as recent history. It shows the declared daily
limit rather than inventing a daily usage total from the snapshot's bounded window.
Household-wide usage-origin reporting remains on the global Tasks page; resident
tasks retain their individual run accounting. Configuration edits remain in the
existing Settings editor, linked from Memory, Skills, and Access. The generic task
form starts empty, and the obsolete fictional/read-only footer was removed.

The three sample-data studies remain available for comparison. They are separate
from the production resident page and submit no work.

## Validation

- 199 frontend tests passed, including tab navigation, deep links, and retained
  task/configuration drafts; TypeScript
  and production build passed.
- Chromium exercised all seven tabs in each variant at 1440, 390, and 320 pixels,
  with no document overflow or JavaScript errors. Checks also covered arrow-key
  navigation, task details, Escape to close dialogs, task preview submission,
  filtering, and the blocked state.
- Desktop and mobile screenshots were visually reviewed. These are sample-data
  design checks, not live resident workflow acceptance.

- The actual authenticated Docker frontend was checked at 1440, 390, and 320
  pixels. All seven tabs, retained task drafts, and journal deep links passed
  without API mutations or document overflow. Desktop rail spacing and mobile
  layouts were visually reviewed.
