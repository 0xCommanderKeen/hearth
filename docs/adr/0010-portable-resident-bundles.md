# Portable, definition-only resident bundles

Status: accepted, 2026-09-07.

The operator asked for a way to export a resident so it can be shared and imported into
another Hearth, and for resident creation to support that. This is a deliberate, narrow
departure from the rule "do not add migration, historical compatibility or cross-system
transfer machinery" in `AGENTS.md` and `docs/rebuild-plan.md`. The departure is bounded
as follows and both documents now name this ADR as the exception.

**A bundle is a definition, not a record.** It carries what the operator would type to
create the resident again: declaration, current memory text, the exact content of the
pinned skill revisions and selected input sets, and one daily routine. It never carries
runs, tasks, artifacts, usage, audit, lifecycle state, operation receipts or skill
validation evidence. Imported skills are unvalidated catalog entries. This keeps
`backup-restore.md` as the only whole-store mechanism and keeps restored copies held.

**Content, not identities.** Skill and input-set ids are per-database UUIDs. Bundles embed
content and the existing content digests; import reuses matching catalog entries and
creates the rest. Source ids appear only as provenance. Nothing in a bundle can address
records in the receiving store.

**No authority travels.** A carried management grant is informational. The imported
resident starts with no management tools, the same as any newly created child. The
operator grants authority explicitly, on the receiving installation, with its own
revision history.

**No schema change, no compatibility layer.** The schema remains exact-match with no
migrations. Import is ordinary provisioning with a content-materialisation step in the
same writer; its durable receipt is the existing `resident_provisioning` row and inner
operations derive their command ids from the import key. Only `bundle_version: 1` is
accepted; there is no converter for other versions or for foreign formats. Old
prototype databases are still refused.

**Execution profile is the receiver's.** The bundle records the exported profile for
information; the import always uses the installation's configured runtime and reports
the substitution.

Consequences: a failed provisioning after materialisation leaves the created skills and
inputs in the library so the generic retry can replay; names are not unique and the
operator may override them on import; a resident with more than one routine cannot be
exported until provisioning accepts more than one. See `docs/resident-bundles.md`.
