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
| Spawn before acknowledgement crash | Discover existing runtime, avoid duplicate | Pending runtime slice |
| Stale completion after replacement | Refuse former ownership token | Pending runtime slice |
| Cancellation while runtime unreachable | Visible stopping/unknown; block replacement | Pending recovery slice |
| Zero exit without valid output | Runtime completion is not task success | Pending artifact slice |
| Missing usage | Unknown, not zero; pause capped execution | Pending accounting slice |
| Midnight with unresolved reservation | Carry unresolved exposure into new admission | Foundation tests |
| Approval races expiry or changed payload | First valid exact-action decision only | Pending approval slice |
| Agent tries bypassing approval | Credential/tool path refuses action | Pending broker slice |
| Side effect accepted, acknowledgement lost | Reconcile; no blind non-idempotent retry | Pending broker slice |
| Notification accepted, acknowledgement lost | Durable at-least-once retry with dedupe ID | Pending delivery slice |
| Schedule replay/DST/catch-up | Unique occurrence and bounded catch-up | Pending routine slice |
| SSE gap or stale evidence | Reset snapshot / explicit unknown | Pending observation slice |
| Disk full or missing artifact | No false durable success | Pending storage slice |
| Restore, import twice, rollback after new work | Consistent files/state; preserve post-transfer work | Pending migration slice |
| Two versions try to execute same resident | Only designated owner may launch | Pending cutover guard |

