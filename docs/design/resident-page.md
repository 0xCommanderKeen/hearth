# Resident page design review

2026-09-10 · Three interactive layout studies, pending operator preference.

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

## Integration after selection

Use live snapshot fields for presence, known usage, and actual task counts; retain
unknown/stale distinctions and never infer idle from a missing event. Map memory
and journal into Memory, provenance and instructions into Skills, grants and
mounted inputs into Access, and runtime/lifecycle controls into Settings. Keep
correspondence and scheduled work discoverable in Tasks, with their existing full
editors reachable from there. Preserve run, journal, and correspondence deep links
by selecting the owning tab. Retain unsent drafts across tab switches, and retain
restore/read-only gates and pending-command identity across store changes.

The prototypes are a design comparison, not a replacement of those live behaviors.
The selected design should be wired into the resident page in a subsequent change.

## Validation

- 197 existing frontend tests passed after updating the task wording; TypeScript
  and production build passed.
- Chromium exercised all seven tabs in each variant at 1440, 390, and 320 pixels,
  with no document overflow or JavaScript errors. Checks also covered arrow-key
  navigation, task details, Escape to close dialogs, task preview submission,
  filtering, and the blocked state.
- Desktop and mobile screenshots were visually reviewed. These are sample-data
  design checks, not live resident workflow acceptance.
