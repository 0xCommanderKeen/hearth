# Behavioral acceptance matrix

Port guarantees from Warren's documented transitions and regression scenarios,
not its private mocks or event names. Tests marked pending are not yet guarantees.

| Scenario | Required outcome | Evidence |
| --- | --- | --- |
| Lost submission response, retry same key | Same task and receipt; one audit fact | Foundation tests |
| Same key with changed payload | Conflict, no second task | Foundation tests |
| Expired command | Refuse even when replaying; retain tombstone | Foundation tests |
| Two resident edits from same revision | Exactly one applies | Foundation tests |
| Concurrent admission for one resident | One active run; losing task stays queued | Foundation tests |
| Insufficient daily allowance | No run or reservation created | Foundation tests |
| Audit append fails | Operational mutation rolls back | Foundation tests |
| Process restart | Committed command/task/run survive | Foundation tests |
| Crash during schema migration | No partial version/schema | Foundation tests |
| Newer unsupported schema | Refuse to operate or downgrade it | Foundation tests |
| Spawn before acknowledgement crash | Discover existing runtime, avoid duplicate | `test_execution.py`: launch acknowledgement loss and stale-owner tests (mock) |
| Stale completion after replacement | Refuse former ownership token | `test_execution.py`: launch acknowledgement loss and stale-owner tests (mock) |
| Cancellation while runtime unreachable | Visible stopping/unknown; block replacement | `test_execution.py`: cancellation and lost-evidence tests (mock) |
| Zero exit without valid output | Runtime completion is not task success | `test_execution.py`: successful output required (mock; semantic quality remains pending) |
| Missing usage | Unknown, not zero; pause capped execution | `test_execution.py`: unknown usage pauses admission; operator resume cannot clear it (`test_pause.py`) |
| Midnight with unresolved reservation | Carry unresolved exposure into new admission | Foundation tests |
| Approval races expiry or changed payload | First valid exact-action decision only | `test_authority.py`: concurrent decisions, exclusive expiry, reviewed digest and revision checks |
| Agent tries bypassing approval | Credential/tool path refuses action | Broker mock behavior tested in `test_authority.py`; runtime-origin bypass enforcement still pending |
| Side effect accepted, acknowledgement lost | Reconcile; no blind non-idempotent retry | Broker mock behavior tested in `test_authority.py`; runtime-origin bypass enforcement still pending |
| Notification accepted, acknowledgement lost | Durable at-least-once retry with dedupe ID | `test_notifications.py`: stable identity, durable backoff and checksummed receipt recovery (local mock) |
| Schedule replay/DST/catch-up | Unique occurrence and bounded catch-up | `test_routines.py`: concurrent/restarted ticks, fold/gap, latest-only catch-up, overlap skipping |
| SSE gap or stale evidence | Reset snapshot / explicit unknown | `test_api.py` plus browser tests: epoch/cursor reset, unknown ownership, stale connection display |
| Disk full or missing artifact | No false durable success | Injected write/audit failures and missing/corrupt artifact tests pass; actual-host disk exhaustion remains pending |
| Restore, import twice, rollback after new work | Consistent files/state; preserve post-transfer work | `test_backup.py`: consistent held restore passes; repeated import and post-transfer rollback pending |
| Two versions try to execute same resident | Only designated owner may launch | Pending cutover guard |


Manual operator pause/resume is covered by `test_pause.py`, authenticated API tests,
and a browser control test. It gates new admission, does not claim existing runs
stopped, and cannot clear accounting holds. All execution/effect evidence above is
mock evidence. The matrix does not establish host isolation, real provider behavior,
production recovery, ownership transfer, or elapsed canary acceptance.
