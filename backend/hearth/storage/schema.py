# ruff: noqa: E501
"""The complete schema for a fresh Hearth database; no historical upgrades."""

SCHEMA = (
    """CREATE TABLE skill_validations (
        id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, candidate_revision INTEGER NOT NULL,
        candidate_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
        evaluator_id TEXT REFERENCES residents(id), evaluator_revision INTEGER,
        actor TEXT NOT NULL, originating_run_id TEXT REFERENCES runs(id), grant_revision INTEGER,
        reserve INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        request_sha256 TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','passed','failed')),
        reason TEXT, UNIQUE(skill_id,candidate_revision),
        FOREIGN KEY(skill_id,candidate_revision) REFERENCES skill_revisions(skill_id,revision)
    )""",
    """CREATE TABLE skill_validation_cases (
        validation_id TEXT NOT NULL REFERENCES skill_validations(id), position INTEGER NOT NULL,
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id), run_id TEXT UNIQUE REFERENCES runs(id),
        input_set_id TEXT NOT NULL, input_revision INTEGER NOT NULL, input_sha256 TEXT NOT NULL,
        result TEXT, PRIMARY KEY(validation_id,position), CHECK(position IN (0,1)),
        FOREIGN KEY(input_set_id,input_revision) REFERENCES input_revisions(input_set_id,revision)
    )""",
    """CREATE TABLE skill_publications (
        skill_id TEXT NOT NULL, revision INTEGER NOT NULL, candidate_revision INTEGER NOT NULL,
        validation_id TEXT NOT NULL UNIQUE REFERENCES skill_validations(id), sha256 TEXT NOT NULL,
        PRIMARY KEY(skill_id,revision),
        FOREIGN KEY(skill_id,revision) REFERENCES skill_revisions(skill_id,revision),
        FOREIGN KEY(skill_id,candidate_revision) REFERENCES skill_revisions(skill_id,revision)
    )""",
    """CREATE TABLE skill_authoring_revisions (
        skill_id TEXT NOT NULL, revision INTEGER NOT NULL, manifest TEXT NOT NULL,
        sha256 TEXT NOT NULL, structure TEXT NOT NULL, PRIMARY KEY(skill_id,revision),
        FOREIGN KEY(skill_id,revision) REFERENCES skill_revisions(skill_id,revision)
    )""",
    """CREATE TABLE resident_lifecycle (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id), revision INTEGER NOT NULL CHECK(revision>=0),
        FOREIGN KEY(resident_id,revision) REFERENCES resident_lifecycle_history(resident_id,revision)
    )""",
    """CREATE TABLE resident_lifecycle_history (
        resident_id TEXT NOT NULL REFERENCES residents(id), revision INTEGER NOT NULL CHECK(revision>=0),
        content TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(resident_id,revision)
    )""",
    """CREATE TABLE resident_maintenance_operations (
        command_id TEXT PRIMARY KEY, actor TEXT NOT NULL, payload_digest TEXT NOT NULL,
        receipt TEXT NOT NULL, receipt_sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE run_management (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), resident_id TEXT NOT NULL REFERENCES residents(id),
        grant_revision INTEGER, grant_sha256 TEXT, expires_at INTEGER NOT NULL,
        thread_id TEXT, turn_id TEXT, catalog_sha256 TEXT, tools_sha256 TEXT,
        FOREIGN KEY(resident_id,grant_revision) REFERENCES management_grant_revisions(resident_id,revision)
    )""",
    """CREATE TABLE management_calls (
        run_id TEXT NOT NULL REFERENCES run_management(run_id), call_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL, response TEXT NOT NULL, recorded_at INTEGER NOT NULL,
        PRIMARY KEY(run_id,call_id)
    )""",
    """CREATE TABLE management_operations (
        resident_id TEXT NOT NULL REFERENCES residents(id), operation_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL, originating_run_id TEXT NOT NULL REFERENCES runs(id),
        receipt TEXT NOT NULL, PRIMARY KEY(resident_id,operation_id)
    )""",
    """CREATE TABLE management_grants (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id), revision INTEGER NOT NULL CHECK(revision>0),
        FOREIGN KEY(resident_id,revision) REFERENCES management_grant_revisions(resident_id,revision) DEFERRABLE INITIALLY DEFERRED
    )""",
    """CREATE TABLE management_grant_revisions (
        resident_id TEXT NOT NULL REFERENCES residents(id), revision INTEGER NOT NULL CHECK(revision>0),
        policy TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(resident_id,revision)
    )""",
    """CREATE TABLE input_selections (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id), revision INTEGER NOT NULL CHECK(revision>=0),
        count INTEGER NOT NULL CHECK(count BETWEEN 0 AND 4), sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE selected_inputs (
        resident_id TEXT NOT NULL REFERENCES input_selections(resident_id), position INTEGER NOT NULL,
        input_set_id TEXT NOT NULL REFERENCES input_sets(id), PRIMARY KEY(resident_id,position), UNIQUE(resident_id,input_set_id)
    )""",
    """CREATE TABLE input_selection_operations (
        command_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, receipt TEXT NOT NULL
    )""",
    """CREATE TABLE run_input_sets (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), resident_id TEXT NOT NULL REFERENCES residents(id),
        revision INTEGER NOT NULL, count INTEGER NOT NULL CHECK(count BETWEEN 0 AND 4), sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE run_inputs (
        run_id TEXT NOT NULL REFERENCES run_input_sets(run_id), position INTEGER NOT NULL,
        input_set_id TEXT NOT NULL, input_revision INTEGER NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(run_id,position), UNIQUE(run_id,input_set_id),
        FOREIGN KEY(input_set_id,input_revision) REFERENCES input_revisions(input_set_id,revision)
    )""",
    """CREATE TABLE input_sets (
        id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>0),
        created_by TEXT NOT NULL, created_at INTEGER NOT NULL,
        FOREIGN KEY(id,revision) REFERENCES input_revisions(input_set_id,revision) DEFERRABLE INITIALLY DEFERRED
    )""",
    """CREATE TABLE input_revisions (
        input_set_id TEXT NOT NULL REFERENCES input_sets(id), revision INTEGER NOT NULL CHECK(revision>0),
        name TEXT NOT NULL, notes TEXT NOT NULL, sha256 TEXT NOT NULL, edited_by TEXT NOT NULL, edited_at INTEGER NOT NULL,
        PRIMARY KEY(input_set_id,revision)
    )""",
    """CREATE TABLE input_operations (
        command_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, receipt TEXT NOT NULL
    )""",
    """CREATE TABLE resident_provisioning (
        command_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, resident_id TEXT NOT NULL UNIQUE,
        request TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('setup','ready','failed')),
        reason TEXT, creator TEXT NOT NULL, manager TEXT NOT NULL, originating_run_id TEXT,
        created_at INTEGER NOT NULL, routine_id TEXT REFERENCES routines(id), task_id TEXT REFERENCES tasks(id),
        name TEXT NOT NULL
    )""",
    """CREATE TABLE resident_profiles (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id), command_id TEXT NOT NULL UNIQUE REFERENCES resident_provisioning(command_id),
        creator TEXT NOT NULL, manager TEXT NOT NULL, originating_run_id TEXT,
        created_at INTEGER NOT NULL, creation_reason TEXT NOT NULL,
        execution_profile TEXT NOT NULL
    )""",
    """CREATE TABLE resident_skill_sets (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id), revision INTEGER NOT NULL,
        count INTEGER NOT NULL, sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE assigned_skills (
        resident_id TEXT NOT NULL REFERENCES resident_skill_sets(resident_id), position INTEGER NOT NULL,
        skill_id TEXT NOT NULL, skill_revision INTEGER NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(resident_id, position), UNIQUE(resident_id, skill_id),
        FOREIGN KEY(skill_id, skill_revision) REFERENCES skill_revisions(skill_id, revision)
    )""",
    """CREATE TABLE assignment_operations (
        command_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, receipt TEXT NOT NULL
    )""",
    """CREATE TABLE run_skill_sets (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), resident_id TEXT NOT NULL REFERENCES residents(id),
        revision INTEGER NOT NULL, count INTEGER NOT NULL, sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE run_skills (
        run_id TEXT NOT NULL REFERENCES run_skill_sets(run_id), position INTEGER NOT NULL,
        skill_id TEXT NOT NULL, skill_revision INTEGER NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(run_id, position), UNIQUE(run_id, skill_id),
        FOREIGN KEY(skill_id, skill_revision) REFERENCES skill_revisions(skill_id, revision)
    )""",
    """CREATE TABLE skills (
        id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision > 0),
        created_by TEXT NOT NULL, created_at INTEGER NOT NULL,
        FOREIGN KEY(id, revision) REFERENCES skill_revisions(skill_id, revision)
            DEFERRABLE INITIALLY DEFERRED
    )""",
    """CREATE TABLE skill_revisions (
        skill_id TEXT NOT NULL REFERENCES skills(id), revision INTEGER NOT NULL CHECK(revision > 0),
        name TEXT NOT NULL, description TEXT NOT NULL, instructions TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('active','archived','draft')),
        edited_by TEXT NOT NULL, edited_at INTEGER NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(skill_id, revision)
    )""",
    """CREATE TABLE skill_operations (
        command_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, skill_id TEXT NOT NULL,
        revision INTEGER NOT NULL, receipt TEXT NOT NULL,
        FOREIGN KEY(skill_id, revision) REFERENCES skill_revisions(skill_id, revision)
    )""",
    """CREATE TABLE run_household_windows (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), timezone TEXT NOT NULL,
        budget_day TEXT NOT NULL, starts_at INTEGER NOT NULL, ends_at INTEGER NOT NULL,
        policy_revision INTEGER NOT NULL
    )""",
    """CREATE TABLE household_policy (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
        daily_limit INTEGER NOT NULL CHECK(daily_limit>=0), timezone TEXT NOT NULL,
        resident_limit INTEGER NOT NULL CHECK(resident_limit BETWEEN 1 AND 1000),
        concurrency_limit INTEGER NOT NULL CHECK(concurrency_limit BETWEEN 1 AND 100),
        journal_limit INTEGER NOT NULL CHECK(journal_limit BETWEEN 1 AND 1000)
    )""",
    """CREATE TABLE approvals (
        id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
        resident_id TEXT NOT NULL REFERENCES residents(id),
        payload TEXT NOT NULL, digest TEXT NOT NULL,
        expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','approved','denied','expired')),
        decided_at INTEGER
    )""",
    """CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
        relative_path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
        size INTEGER NOT NULL CHECK (size >= 0), simulated INTEGER NOT NULL CHECK (simulated IN (0,1))
    )""",
    """CREATE TABLE audit (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        at INTEGER NOT NULL,
        detail TEXT NOT NULL
    )""",
    """CREATE TABLE commands (
        id TEXT PRIMARY KEY,
        payload_digest TEXT NOT NULL,
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
        accepted_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    )""",
    """CREATE TABLE declarations (
        resident_id TEXT NOT NULL REFERENCES residents(id),
        revision INTEGER NOT NULL CHECK (revision > 0),
        name TEXT NOT NULL,
        purpose TEXT NOT NULL,
        daily_limit INTEGER NOT NULL CHECK (daily_limit >= 0),
        created_at INTEGER NOT NULL,
        budget_timezone TEXT NOT NULL DEFAULT 'UTC',
        skill_text TEXT NOT NULL DEFAULT '',
        memory_writable INTEGER NOT NULL DEFAULT 0 CHECK (memory_writable IN (0,1)),
        PRIMARY KEY (resident_id, revision)
    )""",
    """CREATE TABLE deliveries (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, resource_id TEXT NOT NULL,
        payload TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','retry','delivered','obsolete')),
        attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL,
        delivered_at INTEGER, reason TEXT,
        UNIQUE(kind, resource_id)
    )""",
    """CREATE TABLE memory_revisions (
        resident_id TEXT NOT NULL REFERENCES residents(id),
        revision INTEGER NOT NULL CHECK(revision > 0), sha256 TEXT NOT NULL,
        size INTEGER NOT NULL CHECK(size >= 0 AND size <= 131072), created_at INTEGER NOT NULL,
        author TEXT NOT NULL CHECK(author IN ('operator','run')),
        PRIMARY KEY(resident_id, revision)
    )""",
    """CREATE TABLE journal_entries (
        resident_id TEXT NOT NULL REFERENCES residents(id),
        sequence INTEGER NOT NULL CHECK(sequence > 0),
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(id), at INTEGER NOT NULL,
        sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size > 0 AND size <= 4096),
        text TEXT NOT NULL, PRIMARY KEY(resident_id, sequence)
    )""",
    """CREATE TABLE journal_archives (
        resident_id TEXT NOT NULL REFERENCES residents(id),
        sequence INTEGER NOT NULL CHECK(sequence > 0),
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(id), at INTEGER NOT NULL,
        sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size > 0 AND size <= 4608),
        PRIMARY KEY(resident_id, sequence)
    )""",
    """CREATE TABLE memory_operations (
        run_id TEXT NOT NULL REFERENCES runs(id), operation_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL, receipt TEXT NOT NULL,
        PRIMARY KEY(run_id, operation_id)
    )""",
    """CREATE TABLE occurrences (
        routine_id TEXT NOT NULL, scheduled_at INTEGER NOT NULL, revision INTEGER NOT NULL,
        task_id TEXT UNIQUE REFERENCES tasks(id),
        status TEXT NOT NULL CHECK (status IN ('queued','skipped_overlap')),
        created_at INTEGER NOT NULL,
        PRIMARY KEY(routine_id, scheduled_at),
        FOREIGN KEY(routine_id, revision) REFERENCES routine_revisions(routine_id, revision)
    )""",
    """CREATE TABLE pauses (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id), reason TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(id), created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE publication_actions (
        id TEXT PRIMARY KEY REFERENCES approvals(id),
        destination TEXT NOT NULL REFERENCES publication_targets(id),
        digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('executing','unknown','completed','refused')),
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        reason TEXT, receipt TEXT
    )""",
    """CREATE TABLE publication_policies (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id),
        revision INTEGER NOT NULL CHECK (revision > 0),
        enabled INTEGER NOT NULL CHECK (enabled IN (0,1))
    )""",
    """CREATE TABLE publication_targets (
        id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision > 0)
    )""",
    """CREATE TABLE residents (
        id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL CHECK (revision > 0)
    )""",
    """CREATE TABLE routine_revisions (
        routine_id TEXT NOT NULL REFERENCES routines(id), revision INTEGER NOT NULL,
        instruction TEXT NOT NULL, local_time TEXT NOT NULL, timezone TEXT NOT NULL,
        created_at INTEGER NOT NULL, PRIMARY KEY(routine_id, revision)
    )""",
    """CREATE TABLE routines (
        id TEXT PRIMARY KEY, resident_id TEXT NOT NULL REFERENCES residents(id),
        revision INTEGER NOT NULL, enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
        next_at INTEGER NOT NULL
    )""",
    """CREATE TABLE run_credentials (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), digest TEXT NOT NULL,
        expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, revoked_at INTEGER
    )""",
    """CREATE TABLE run_pricing (
        run_id TEXT PRIMARY KEY REFERENCES runs(id),
        model TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('standard','fast')),
        schedule TEXT NOT NULL
    )""",
    """CREATE TABLE run_usage (
        run_id TEXT PRIMARY KEY REFERENCES run_pricing(run_id),
        receipt TEXT NOT NULL, sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE run_memory (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), resident_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        FOREIGN KEY(resident_id, revision) REFERENCES memory_revisions(resident_id, revision)
    )""",
    """CREATE TABLE run_journal (
        run_id TEXT NOT NULL REFERENCES runs(id), position INTEGER NOT NULL CHECK(position >= 0),
        resident_id TEXT NOT NULL REFERENCES residents(id), sequence INTEGER NOT NULL CHECK(sequence > 0),
        entry_run_id TEXT NOT NULL REFERENCES runs(id), at INTEGER NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(run_id, position), UNIQUE(run_id, sequence)
    )""",
    """CREATE TABLE "runs" (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        resident_id TEXT NOT NULL, resident_revision INTEGER NOT NULL,
        owner_token TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN
            ('starting','running','stopping','interrupted','succeeded','failed','cancelled')),
        reserved INTEGER NOT NULL CHECK (reserved >= 0),
        budget_day TEXT NOT NULL, created_at INTEGER NOT NULL,
        actual_cost INTEGER CHECK (actual_cost >= 0),
        usage_known INTEGER NOT NULL DEFAULT 0 CHECK (usage_known IN (0,1)),
        finished_at INTEGER,
        artifact_id TEXT,
        cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requested IN (0,1)),
        launch_attempted INTEGER NOT NULL DEFAULT 0 CHECK (launch_attempted IN (0,1)),
        budget_timezone TEXT NOT NULL DEFAULT 'UTC',
        runtime_kind TEXT NOT NULL CHECK(runtime_kind IN ('inline_mock','process_mock','codex_mock','codex_subscription')),
        runtime_version INTEGER NOT NULL CHECK(runtime_version = 1),
        input_digest TEXT NOT NULL,
        FOREIGN KEY(resident_id,resident_revision) REFERENCES declarations(resident_id,revision)
    )""",
    """CREATE TABLE system_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    """CREATE TABLE "tasks" (
        id TEXT PRIMARY KEY, resident_id TEXT NOT NULL REFERENCES residents(id),
        instruction TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN
            ('queued','starting','running','stopping','interrupted','succeeded','failed','cancelled')),
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE usage_reconciliations (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), command_id TEXT NOT NULL UNIQUE,
        digest TEXT NOT NULL, amount INTEGER NOT NULL CHECK (amount >= 0),
        evidence TEXT NOT NULL, recorded_at INTEGER NOT NULL,
        source TEXT NOT NULL CHECK (source='operator_reported_mock')
    )""",
    """CREATE UNIQUE INDEX active_resident ON runs(resident_id)
        WHERE status IN ('starting','running','stopping','interrupted')""",
    """CREATE UNIQUE INDEX active_task ON runs(task_id)
        WHERE status IN ('starting','running','stopping','interrupted')""",
    """CREATE UNIQUE INDEX publication_claim ON publication_actions(destination)
        WHERE status IN ('executing','unknown')""",
)
