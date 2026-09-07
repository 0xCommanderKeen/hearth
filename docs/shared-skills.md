# Shared skills

Townhall → Skills is the reusable catalog. Create a skill with a name, usage
guidance and Markdown instructions; open its address after refresh, save a new
revision, inspect older revisions, or archive it. Archiving preserves content and
history and excludes the skill from the default active catalog. Resident attachment
and run pinning are delivered separately in #87; this catalog grants no execution
or management authority.

Setting Karen up also seeds the etiquette skills every household starts with:
**Create residents**, **Create good skills** and **Keep a journal**. They are ordinary
library entries with ordinary revisions, so an operator can read, edit or archive them.
"Keep a journal" is the wording a resident that may write its own memory and journal
follows — one short dated entry per run, only durable facts in memory, never an invented
entry — and it is attached to a resident provisioned with writable memory at its current
revision, when that revision is active and the requested set leaves room inside both
assignment bounds. An operator revising the wording with examples leaves a draft current
until publication; provisioning skips the etiquette while that lasts rather than refusing.
Like every skill, it grants nothing.

Names are bounded to 120 characters, descriptions to 2,000 and instructions to
32,000. All three require non-whitespace text. Exact text is stored without
execution. Preview supports headings, paragraphs, unordered lists, fenced code,
inline code and bold emphasis; HTML and other markup remain inert text. Preview
does not load remote images or make source links executable.

The authenticated operator API owns the same operations used by the browser:

- `GET /api/skills?query=...&include_archived=true` searches names and usage guidance.
- `POST /api/skills` creates from `name`, `description`, `instructions`.
- `GET /api/skills/{id}?revision=1` reads current or exact historical content.
- `PUT /api/skills/{id}` saves the three fields with `expected_revision`.
- `GET /api/skills/{id}/history` reads immutable revisions, newest first.
- `POST /api/skills/{id}/archive` takes `expected_revision`.
- `GET /api/skills/operations/{command_id}` recovers an accepted operation receipt.

Every mutation requires `Idempotency-Key`. The durable receipt binds command,
skill, revision, operation, authenticated actor and time. Repeating the exact
request returns the original receipt; reusing its key for another payload refuses
the change. New mutations of archived skills are refused. A stale expected revision
returns a conflict. The browser retains that draft until the operator explicitly
loads the current revision; an unconfirmed response instead locks the exact pending
request for retry. The route supplies `operator` provenance; caller-supplied actor
or timestamp fields are rejected. Future scoped agent routes must derive identity
from their own authenticated authority, never request text.

`hearth.skills.catalog.Skills` owns transactions; catalog pointer, immutable
revision, content digest, operation receipt and audit commit together. Revision
content uses SHA256 over the canonical sorted JSON object containing name,
description and instructions. Inspection and current-data backup verify the digest.
The complete catalog, receipts and audit survive backup/held restore. The restored
copy remains read-only. There is no historical-schema upgrade or import path.
