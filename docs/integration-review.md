# Mock stack integration review — 2026-09-06

Scope: bootstrap `fe8bf69` through `0705c55`, fifteen implementation slices,
plus the fixes tracked by issue #31. This review supports integrating the mock
implementation. It does not establish real-host, migration, canary or retirement
acceptance. Both reviewers inspected the same fixed diff independently.

## Standards

The reviewer reproduced malformed runtime terminal evidence stalling unrelated
residents while leaving the corrupt run visibly running. This violated truthful
unknown execution and interface failure handling. The runtime now validates costs
and output before returning evidence. Malformed evidence becomes unknown, preserving
ownership while another resident can progress. Eight regression variants cover
invalid scalar costs and empty/non-string/oversized output. Follow-up review: clear.

Nonblocking judgment: several private-named helpers are used across owning modules.
Clarify those actual shared interfaces when changing their boundaries; no generic
persistence abstraction is needed merely to address naming.

## Spec

The reviewer reproduced publication recovery accepting a record whose content was
removed. This violated the promise to keep mismatched evidence unknown with the
destination held. Recovery now hashes actual stored bytes and compares them with
the approved artifact checksum. Missing content, changed content and a recomputed
forged receipt checksum retain the claim; restoring correct evidence completes
reconciliation without resending. Follow-up review: clear.

Stale UTC-budget, pending-feature and CI-blocked statements in slice documentation
were refreshed. Remaining live/cross-system requirements remain explicitly open.

## Rendered browser evidence

Installed headless Chromium exercised a fresh local synthetic dataset. Desktop
1440×1100 and mobile 390×844 screenshots were inspected: Hamlet and Townhall remain
readable, with no horizontal overflow or page errors in the baseline task journey.
Login, Reader setup, task completion, result and local notification state appeared
in the actual rendered browser. Images are local review artifacts outside Git.
This replaces the previous missing rendered-browser evidence; it does not claim a
native accessibility-tree or assistive-technology audit.

All sixteen PRs #2–#32, including these fixes, are merged into main at `a84bed6`.
Main CI `34023432571` passed. Nothing was deployed or activated by merge.

The extended rendered journey also passed: read result, pause/resume new work,
enable/disable daily routine, request/review/approve/publish a local mock summary,
and lock the session. Desktop/mobile approved-state screenshots were captured;
mobile inspection found no overflow. No page errors occurred. Two initial smoke
selectors were corrected to the actual interface labels; no product defect was
found in those selector failures.

Full integrated `make check`: 236 backend tests, 25 browser tests, lint/types,
production build and packaged wheel checks pass. Both recovery reviewers reran
the relevant tests and reported no remaining blocker in the fixes.
