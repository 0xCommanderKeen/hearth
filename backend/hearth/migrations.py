"""Add execution outcomes without discarding foundation identities or receipts."""

import sqlite3
import uuid


def execution_schema(db: sqlite3.Connection) -> None:
    """Rebuild constrained tables inside the caller's migration transaction."""
    db.execute("""CREATE TABLE tasks_next (
        id TEXT PRIMARY KEY, resident_id TEXT NOT NULL REFERENCES residents(id),
        instruction TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN
            ('queued','starting','running','stopping','interrupted','succeeded','failed','cancelled')),
        created_at INTEGER NOT NULL
    )""")
    db.execute("INSERT INTO tasks_next SELECT * FROM tasks")
    db.execute("""CREATE TABLE runs_next (
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
        FOREIGN KEY(resident_id,resident_revision) REFERENCES declarations(resident_id,revision)
    )""")
    db.execute("""INSERT INTO runs_next
        (id,task_id,resident_id,resident_revision,owner_token,status,reserved,budget_day,created_at)
        SELECT id,task_id,resident_id,resident_revision,owner_token,status,
            reserved,budget_day,created_at
        FROM runs""")
    db.execute("DROP TABLE runs")
    db.execute("DROP TABLE tasks")
    db.execute("ALTER TABLE tasks_next RENAME TO tasks")
    db.execute("ALTER TABLE runs_next RENAME TO runs")
    active = "status IN ('starting','running','stopping','interrupted')"
    db.execute(f"CREATE UNIQUE INDEX active_resident ON runs(resident_id) WHERE {active}")
    db.execute(f"CREATE UNIQUE INDEX active_task ON runs(task_id) WHERE {active}")
    db.execute("""CREATE TABLE pauses (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id), reason TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(id), created_at INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
        relative_path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
        size INTEGER NOT NULL CHECK (size >= 0), simulated INTEGER NOT NULL CHECK (simulated = 1)
    )""")


def observation_schema(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE system_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO system_meta VALUES (?, ?)", ("epoch", str(uuid.uuid4())))


def approval_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE publication_policies (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id),
        revision INTEGER NOT NULL CHECK (revision > 0),
        enabled INTEGER NOT NULL CHECK (enabled IN (0,1))
    )""")
    db.execute("""CREATE TABLE publication_targets (
        id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision > 0)
    )""")
    db.execute("INSERT INTO publication_targets VALUES ('mock-noticeboard', 1)")
    db.execute("""CREATE TABLE approvals (
        id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
        resident_id TEXT NOT NULL REFERENCES residents(id),
        payload TEXT NOT NULL, digest TEXT NOT NULL,
        expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','approved','denied','expired')),
        decided_at INTEGER
    )""")
    db.execute("""CREATE TABLE publication_actions (
        id TEXT PRIMARY KEY REFERENCES approvals(id),
        destination TEXT NOT NULL REFERENCES publication_targets(id),
        digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('executing','unknown','completed','refused')),
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        reason TEXT, receipt TEXT
    )""")
    db.execute("""CREATE UNIQUE INDEX publication_claim ON publication_actions(destination)
        WHERE status IN ('executing','unknown')""")


def routine_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE routines (
        id TEXT PRIMARY KEY, resident_id TEXT NOT NULL REFERENCES residents(id),
        revision INTEGER NOT NULL, enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
        next_at INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE routine_revisions (
        routine_id TEXT NOT NULL REFERENCES routines(id), revision INTEGER NOT NULL,
        instruction TEXT NOT NULL, local_time TEXT NOT NULL, timezone TEXT NOT NULL,
        created_at INTEGER NOT NULL, PRIMARY KEY(routine_id, revision)
    )""")
    db.execute("""CREATE TABLE occurrences (
        routine_id TEXT NOT NULL, scheduled_at INTEGER NOT NULL, revision INTEGER NOT NULL,
        task_id TEXT UNIQUE REFERENCES tasks(id),
        status TEXT NOT NULL CHECK (status IN ('queued','skipped_overlap')),
        created_at INTEGER NOT NULL,
        PRIMARY KEY(routine_id, scheduled_at),
        FOREIGN KEY(routine_id, revision) REFERENCES routine_revisions(routine_id, revision)
    )""")


def notification_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE deliveries (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, resource_id TEXT NOT NULL,
        payload TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','retry','delivered','obsolete')),
        attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL,
        delivered_at INTEGER, reason TEXT,
        UNIQUE(kind, resource_id)
    )""")


def control_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE operator_controls (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id),
        revision INTEGER NOT NULL CHECK (revision > 0),
        paused INTEGER NOT NULL CHECK (paused IN (0,1)), updated_at INTEGER NOT NULL
    )""")


def run_access_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE run_credentials (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), digest TEXT NOT NULL,
        expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, revoked_at INTEGER
    )""")


def accounting_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE usage_reconciliations (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), command_id TEXT NOT NULL UNIQUE,
        digest TEXT NOT NULL, amount INTEGER NOT NULL CHECK (amount >= 0),
        evidence TEXT NOT NULL, recorded_at INTEGER NOT NULL,
        source TEXT NOT NULL CHECK (source='operator_reported_mock')
    )""")


def budget_zone_schema(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE declarations ADD COLUMN budget_timezone TEXT NOT NULL DEFAULT 'UTC'")
    db.execute("ALTER TABLE runs ADD COLUMN budget_timezone TEXT NOT NULL DEFAULT 'UTC'")


def skill_schema(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE declarations ADD COLUMN skill_text TEXT NOT NULL DEFAULT ''")


def memory_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE memory_revisions (
        resident_id TEXT NOT NULL REFERENCES residents(id),
        revision INTEGER NOT NULL CHECK(revision > 0), sha256 TEXT NOT NULL,
        size INTEGER NOT NULL CHECK(size >= 0 AND size <= 131072), created_at INTEGER NOT NULL,
        PRIMARY KEY(resident_id, revision)
    )""")
    db.execute("""CREATE TABLE run_memory (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), resident_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        FOREIGN KEY(resident_id, revision) REFERENCES memory_revisions(resident_id, revision)
    )""")
