# Durable mock notifications

Terminal runs and new approval requests enqueue a delivery in the same transaction
as their state and audit records. Notification payloads contain only an allowlisted
kind, resource ID, local browser link, and simulation marker. Instructions, output,
credentials and ownership tokens are excluded. Opening a link requires the normal
operator session; notification delivery never decides or consumes an approval.

One stable delivery ID identifies one immutable local inbox file. The worker
commits each attempt and its next retry time before calling this explicitly
idempotent adapter. Retries use the same ID and payload. Backoff starts at two
seconds and doubles to one hour; each pass handles at most 100 due deliveries.
A file lock beside the canonical database path excludes concurrent workers.
Delivery state and acknowledgement audit commit together.

Before retrying an attempted delivery, the worker checks the expected checksummed
receipt. This recovers a lost acknowledgement without rewriting or duplicating the
message, including after approval expiry. Unreadable/corrupt receipts remain visible
as unconfirmed and are checked later. If no receipt exists, expired notifications
and resolved approval requests become obsolete rather than being sent. Run notices
expire after seven days; approval notices share the request's exclusive expiry.
Notifications are created by current operational events.

The authenticated snapshot and local browser inbox distinguish queued, unconfirmed,
delivered and obsolete records. Links open exact approval previews or results even
outside the recent task/approval lists. Browser display does not imply transport
delivery. The supervision loop reports notification errors separately so a delivery
failure does not prevent subsequent run supervision.

Tests cover transaction rollback, safe payloads, restart, competing workers,
provider outage/backoff, lost acknowledgements, expiry and recovery after expiry,
plus authenticated observations and browser links. This adapter only writes local
mock files. A real notification transport remains unselected and requires its own
idempotency/receipt contract and credentials; no external messages have been sent.
Native visual QA and retention/pruning remain later gates.
