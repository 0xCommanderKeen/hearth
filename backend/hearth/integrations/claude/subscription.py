"""Configuring the Claude subscription: a pinned binary, a pinned CLI and a private login.

No run executes on this runtime yet. This is the seam the headless worker (#146), the
management bridge (#147) and the per-resident runtime (#148) are built on: the kind
exists, the store can be configured for it, and every way of getting that wrong refuses
by name and writes nothing.
"""

import hashlib
import subprocess
import time
from pathlib import Path

from hearth.integrations.claude.config import (
    BINARY_PIN,
    KIND,
    MODEL,
    PROBE_TIMEOUT,
    VERSION,
    environment,
    logged_in,
)
from hearth.integrations.interface import Evidence
from hearth.residents.models import Refused
from hearth.storage.database import Database
from hearth.work.service import _audit


class ClaudeLiveRuntime:
    kind = KIND
    version = 1

    def __init__(self, data: Path, *, binary: Path | None = None, config_dir: Path | None = None):
        self.data = data.resolve()
        self.database = Database(self.data / "hearth.db")
        self.root = self.data / "claude-live"
        if self.database.restored():
            return
        if binary is None or config_dir is None:
            raise Refused("claude_subscription_configuration_required")
        self.binary = binary.resolve()
        self.config_dir = config_dir.resolve()
        # The CLI creates a config directory it is pointed at. Hearth refuses first, so
        # a missing private login is a refusal rather than a new empty login.
        if not self.config_dir.is_dir():
            raise Refused("claude_subscription_login_required")
        env = environment(self.config_dir)
        version = subprocess.check_output(
            [str(self.binary), "--version"], env=env, text=True, timeout=PROBE_TIMEOUT
        ).strip()
        if version != VERSION:
            raise Refused("claude_subscription_version_unsupported")
        try:
            status = subprocess.check_output(
                [str(self.binary), "auth", "status", "--json"],
                env=env,
                text=True,
                timeout=PROBE_TIMEOUT,
            )
        except subprocess.CalledProcessError:
            # The answer is not evidence of a login, and its output is never kept.
            raise Refused("claude_subscription_login_required") from None
        if not logged_in(status):
            raise Refused("claude_subscription_login_required")
        pin = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        with self.database.transaction(write=True) as db:
            previous = db.execute(
                "SELECT value FROM system_meta WHERE key=?", (BINARY_PIN,)
            ).fetchone()
            if previous is None:
                db.execute("INSERT INTO system_meta VALUES (?,?)", (BINARY_PIN, pin))
                _audit(
                    db,
                    "runtime.claude_subscription_configured",
                    KIND,
                    int(time.time()),
                    {"binary": pin, "version": VERSION, "model": MODEL},
                )
            elif previous[0] != pin:
                raise Refused("claude_subscription_binary_changed")
        self.root.mkdir(mode=0o700, exist_ok=True)

    # A configured runtime still starts nothing in this release. The worker, the
    # stream-json receipt and cancellation arrive with #146; until then every call
    # refuses by name rather than half-launching a session nobody can settle.
    def start(self, run_id: str, instruction: str) -> None:
        raise Refused("claude_subscription_run_unsupported")

    def inspect(self, run_id: str, *, expected_digest: str | None = None) -> Evidence:
        raise Refused("claude_subscription_run_unsupported")

    def stop(self, run_id: str) -> None:
        raise Refused("claude_subscription_run_unsupported")
