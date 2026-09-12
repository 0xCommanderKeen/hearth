# ruff: noqa: E501
"""The complete schema for a fresh Hearth database; no historical upgrades.

`runs.runtime_kind` admits every kind in `integrations.interface.RUNTIMES`: the two
live ones this release can start work on, and the three simulated kinds Hearth used to
ship. The simulated ones are history a forward-upgraded store may still carry, and a
run's own pin is the honest record of where its work happened.
See `database.HISTORICAL_RUNTIME_KINDS`.

`run_mounts` is what a run could reach on disk: the mounts its resident's grant held at
the revision admission pinned, in the order the grant listed them, so a finished run says
what it could see whether or not it executed inside a sandbox
(`docs/adr/0016-sandbox-per-run.md`). A run with no filesystem grant has no rows here.

`runs.login_scope` is which provider login the run was admitted to spend: the
resident's own directory under `credentials/`, or the household's
(`docs/adr/0016-sandbox-per-run.md`). It is resolved once, at admission, and a run
keeps it whatever is seeded or taken away afterwards. Every run that predates the
column spent the household's, which is what the upgrade fills.

`declarations.runtime` is the runtime one resident's work is admitted to, null for a
resident that follows the store's default (`system_meta.runtime_kind`). It carries no
CHECK: a store upgraded forward may hold a kind a later release retired, and refusing
to open it would lose the resident rather than the runtime
(`docs/adr/0015-runtime-per-resident.md`).
"""

SCHEMA = (
    """CREATE TABLE communications_cursors (
        connection_id TEXT NOT NULL, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
        baseline TEXT NOT NULL, cursor TEXT NOT NULL, through_id TEXT,
        updated_at INTEGER NOT NULL, scan_before TEXT, PRIMARY KEY(connection_id,guild_id,channel_id)
    )""",
    """CREATE TABLE communications_schedule (
        kind TEXT NOT NULL CHECK(kind IN ('route','connection','destination','guild','channel')), id TEXT NOT NULL,
        eligible_at INTEGER NOT NULL, error TEXT,
        PRIMARY KEY(kind,id)
    )""",
    """CREATE TABLE run_communications (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), resident_id TEXT NOT NULL REFERENCES residents(id),
        grant_revision INTEGER NOT NULL
    )""",
    """CREATE TABLE communications_revisions (
        kind TEXT NOT NULL CHECK(kind IN ('connection','route','grant')), id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0), content TEXT NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(kind,id,revision)
    )""",
    """CREATE TABLE communications_config (
        kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
        PRIMARY KEY(kind,id), FOREIGN KEY(kind,id,revision) REFERENCES communications_revisions(kind,id,revision)
    )""",
    """CREATE TABLE chat_conversations (
        id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, route_id TEXT NOT NULL,
        guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, sender_id TEXT NOT NULL,
        UNIQUE(connection_id,route_id,guild_id,channel_id,sender_id)
    )""",
    """CREATE TABLE chat_turns (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES chat_conversations(id),
        message_id TEXT NOT NULL, sender_id TEXT NOT NULL, created_at INTEGER NOT NULL,
        text TEXT CHECK(text IS NULL OR length(CAST(text AS BLOB))<=8192),
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id), run_id TEXT UNIQUE REFERENCES runs(id),
        route_revision INTEGER NOT NULL, connection_revision INTEGER NOT NULL, grant_revision INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('working','reply_pending','delivery','closed')),
        reason TEXT, reply_intent TEXT, operation_id TEXT, reply_sha256 TEXT
    )""",
    "CREATE UNIQUE INDEX chat_one_open_turn ON chat_turns(conversation_id) WHERE state!='closed'",
    """CREATE TABLE chat_inbound (
        connection_id TEXT NOT NULL, channel_id TEXT NOT NULL, message_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL, receipt TEXT NOT NULL,
        turn_id TEXT UNIQUE REFERENCES chat_turns(id), decided_at INTEGER NOT NULL,
        PRIMARY KEY(connection_id,channel_id,message_id)
    )""",
    """CREATE TABLE run_conversations (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), turn_id TEXT NOT NULL UNIQUE REFERENCES chat_turns(id),
        context TEXT NOT NULL, sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE skill_validations (
        id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, candidate_revision INTEGER NOT NULL,
        candidate_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
        resident_id TEXT REFERENCES residents(id), resident_revision INTEGER,
        memory_revision INTEGER, context_version INTEGER,
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
    """CREATE TABLE communications_calls (
        run_id TEXT NOT NULL REFERENCES run_management(run_id), call_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL, PRIMARY KEY(run_id,call_id)
    )""",
    """CREATE TABLE communications_requests (
        run_id TEXT NOT NULL REFERENCES run_management(run_id), operation_id TEXT NOT NULL,
        request_digest TEXT NOT NULL, tool TEXT NOT NULL, params TEXT NOT NULL,
        binding TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','reading','complete')),
        result TEXT, created_at INTEGER NOT NULL, completed_at INTEGER, result_sha256 TEXT,
        PRIMARY KEY(run_id,operation_id)
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
    """CREATE TABLE run_mounts (
        run_id TEXT NOT NULL REFERENCES runs(id), position INTEGER NOT NULL,
        grant_revision INTEGER NOT NULL, name TEXT NOT NULL, host_path TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('ro','rw')),
        PRIMARY KEY(run_id,position), UNIQUE(run_id,name)
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
        journal_limit INTEGER NOT NULL CHECK(journal_limit BETWEEN 1 AND 1000),
        max_letter_depth INTEGER NOT NULL CHECK(max_letter_depth BETWEEN 0 AND 5),
        letter_ttl_seconds INTEGER NOT NULL CHECK(letter_ttl_seconds BETWEEN 60 AND 604800),
        letter_daily_limit INTEGER NOT NULL CHECK(letter_daily_limit BETWEEN 0 AND 100)
    )""",
    """CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
        relative_path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
        size INTEGER NOT NULL CHECK (size >= 0)
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
        letters_accept INTEGER NOT NULL DEFAULT 0 CHECK (letters_accept IN (0,1)),
        runtime TEXT,
        PRIMARY KEY (resident_id, revision)
    )""",
    # A letter is an ordinary task with an address: who sent it, from which run and task,
    # the root the chain rolls up to, how many hops in it is, and when it goes stale.
    # The receiver is the task's own resident; there is no second copy of that fact.
    # An operator writing on Hearth's own behalf has no resident and no run behind it,
    # so both sender columns are empty together or not at all.
    # What became of it is one word, written when the letter reaches its end: answered
    # (`replied`), worked and left unanswered, `failed` with the run that worked it, or
    # `expired` before anyone started it. A letter still open is `pending`, and a letter
    # that ended says when, so the sender can read what is new since it last looked.
    """CREATE TABLE letters (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id),
        sender_resident_id TEXT REFERENCES residents(id),
        sender_run_id TEXT REFERENCES runs(id),
        parent_task_id TEXT REFERENCES tasks(id),
        root_task_id TEXT NOT NULL REFERENCES tasks(id),
        depth INTEGER NOT NULL CHECK (depth > 0),
        title TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending','replied','unanswered','failed','expired')),
        settled_at INTEGER,
        CHECK ((sender_resident_id IS NULL) = (sender_run_id IS NULL)),
        CHECK ((state = 'pending') = (settled_at IS NULL))
    )""",
    # The answer the sender reads. One per letter, written by the run that worked it;
    # the full run artifact stays linked for the operator.
    """CREATE TABLE letter_replies (
        task_id TEXT PRIMARY KEY REFERENCES letters(task_id),
        run_id TEXT NOT NULL REFERENCES runs(id),
        resident_id TEXT NOT NULL REFERENCES residents(id),
        text TEXT NOT NULL,
        written_at INTEGER NOT NULL
    )""",
    """CREATE TABLE notifications (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, resource_id TEXT NOT NULL,
        payload TEXT NOT NULL, created_at INTEGER NOT NULL, read_at INTEGER,
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
        runtime_kind TEXT NOT NULL CHECK(runtime_kind IN ('inline_mock','process_mock','codex_mock','codex_subscription','claude_subscription')),
        runtime_version INTEGER NOT NULL CHECK(runtime_version = 1),
        input_digest TEXT NOT NULL,
        login_scope TEXT NOT NULL DEFAULT 'household' CHECK(login_scope IN ('resident','household')),
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
        source TEXT NOT NULL CHECK (source='operator_reported')
    )""",
    """CREATE UNIQUE INDEX active_resident ON runs(resident_id)
        WHERE status IN ('starting','running','stopping','interrupted')""",
    """CREATE UNIQUE INDEX active_task ON runs(task_id)
        WHERE status IN ('starting','running','stopping','interrupted')""",
    """CREATE TABLE delivery_operations (
        id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
        intent TEXT NOT NULL, sha256 TEXT NOT NULL,
        connection_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
        ('queued','dispatching','confirmed','failed','refused','unknown','cancelled','abandoned')),
        revision INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, eligible_at INTEGER NOT NULL,
        parent_id TEXT REFERENCES delivery_operations(id)
    )""",
    """CREATE TABLE delivery_attempts (
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES delivery_operations(id),
        owner TEXT NOT NULL, epoch TEXT NOT NULL, binding_revision INTEGER NOT NULL,
        dispatched_at INTEGER NOT NULL, completed_at INTEGER,
        state TEXT NOT NULL CHECK(state IN
        ('dispatching','confirmed','safe_failure','refused','unknown')),
        receipt TEXT, UNIQUE(operation_id, id)
    )""",
    """CREATE TABLE delivery_bindings (
        connection_id TEXT PRIMARY KEY, bot_id TEXT, epoch TEXT NOT NULL,
        store_path TEXT NOT NULL, revision INTEGER NOT NULL, owner TEXT,
        operator_id TEXT NOT NULL, activated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE notification_forwarding (
        id TEXT PRIMARY KEY, destination TEXT NOT NULL, revision INTEGER NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), kinds TEXT NOT NULL,
        watermark INTEGER NOT NULL, cursor INTEGER NOT NULL,
        activated INTEGER NOT NULL CHECK(activated IN (0,1))
    )""",
    """CREATE TABLE delivery_resolutions (
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES delivery_operations(id),
        revision INTEGER NOT NULL, action TEXT NOT NULL, operator_id TEXT NOT NULL,
        at INTEGER NOT NULL, reason TEXT NOT NULL, evidence TEXT,
        UNIQUE(operation_id, revision)
    )""",
    """CREATE UNIQUE INDEX delivery_inflight ON delivery_attempts(operation_id)
        WHERE state='dispatching'""",
)
