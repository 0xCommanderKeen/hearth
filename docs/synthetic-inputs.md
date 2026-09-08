# Synthetic input sets

Townhall's Synthetic inputs library holds small, named sets of fictional notes.
Create or edit a set there, then choose up to four sets in New resident or the
resident's profile. The profile explicitly shows an empty selection when no sets
are chosen. No filesystem paths, imports, personal sources or connectors are read.

Each save creates an immutable checksummed revision. Resident selections reference
stable set IDs in a deliberate order; future admission resolves their then-current
revisions. Admission pins the exact ordered revisions and content hashes, including
an explicit empty manifest, in the same SQLite writer as the run and budget reserve.
Editing notes or changing selection leaves admitted runs unchanged. Run history
links to the exact historical text, not today's editor.

Context version 5 includes named `inputs`, explicit `input_state` and a source-data
warning, separately from purpose, resident instructions and reusable skills.
The existing `notes` field is a deterministic flattening of those exact pins.
There is no global fallback. Reader setup explicitly creates and selects the named
example once; repeated setup preserves later content edits and selection changes.
Empty inputs are labelled explicitly rather than silently omitted.

The operator API provides GET/POST `/api/input-sets`, GET/PUT
`/api/input-sets/{id}` (GET accepts `revision`), and GET/PUT
`/api/residents/{id}/inputs`. Mutations require an idempotency key; updates also
require the expected content or selection revision. Exact retries return the saved
receipt; conflicts preserve the browser draft. Name length is 120 characters;
a set contains at most 32 notes of 4,000 characters and 32 KiB total UTF-8 content.
The final serialized run context must fit the existing 512 KiB staging limit;
otherwise admission rolls back without reserving budget or creating a run.

A run credential can read only its authoritative pinned context. It cannot browse
the operator library, select arbitrary sets or change resident configuration.
Notes are escaped source text in the browser and confer no management authority.
Missing, altered, reordered or misowned pins refuse dispatch with visible input
provenance errors while healthy work continues. Current-data backup validates
source checksums, selections and run manifests; held restore preserves historical
reads and refuses all writes. Fresh schema only; no data conversion is provided.
