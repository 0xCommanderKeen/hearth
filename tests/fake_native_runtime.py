"""Test-only native receipt runtime for communications' ordinary run bridge."""

from dataclasses import asdict

from hearth.execution.usage import binding
from hearth.integrations.codex.pricing import MODEL
from hearth.integrations.codex.usage import UsageBinding, publish, read
from hearth.integrations.interface import Evidence, encode_receipt
from hearth.management.bridge import BoundRun, Bridge
from hearth.work.service import Hearth

from tests.fake_runtime import BINARY, KIND, FakeRuntime


def native_terminal(pin, *, status="completed", text="A synthetic installed reply") -> dict:
    """The app-server evidence the management runtime seals this run's one turn with."""
    thread, turn = pin["thread_id"], pin["turn_id"]
    return {
        "protocol": "codex-app-server-0.153.4",
        "launched": True,
        "cancelled": False,
        "error": None,
        "exit_code": -15,
        "catalog_sha256": pin["catalog_sha256"],
        "tools_sha256": pin["tools_sha256"],
        "events": [
            {"method": "thread/started", "params": {"thread": {"id": thread, "model": MODEL}}},
            {"method": "turn/started", "params": {"threadId": thread, "turn": {"id": turn}}},
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": thread,
                    "turnId": turn,
                    "tokenUsage": {
                        "total": {
                            "totalTokens": 30,
                            "inputTokens": 20,
                            "outputTokens": 10,
                            "cachedInputTokens": 0,
                            "reasoningOutputTokens": 0,
                        }
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread,
                    "turn": {
                        "id": turn,
                        "status": status,
                        "error": None if status == "completed" else "synthetic failure",
                        "items": [{"type": "agentMessage", "text": text, "phase": "final_answer"}],
                    },
                },
            },
        ],
    }


class FakeNativeRuntime(FakeRuntime):
    def __init__(self, data):
        super().__init__(data, scenario="hold")

    def start(self, run_id, instruction):
        super().start(run_id, instruction)
        if (self.folder(run_id) / "receipt.json").exists():
            return
        service = Hearth(self.database)
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            bound = binding(db, row)
            epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
            owner = row["owner_token"]
        bridge = Bridge(service, BoundRun(run_id, owner, epoch, bound.input_digest))
        bridge.bind_thread("synthetic-thread")
        bridge.bind_turn("synthetic-thread", "synthetic-turn")
        with self.database.transaction(write=True) as db:
            # Synthetic configuration pins, identical to the native owning-interface tests.
            db.execute(
                "UPDATE run_management SET catalog_sha256=?,tools_sha256=? WHERE run_id=?",
                ("b" * 64, "c" * 64, run_id),
            )
            pin = db.execute("SELECT * FROM run_management WHERE run_id=?", (run_id,)).fetchone()
        publish(
            self.folder(run_id) / "receipt.json",
            {
                "kind": KIND,
                "protocol": "management",
                "binding": asdict(bound),
                "binary": BINARY,
                "terminal": native_terminal(pin),
            },
        )

    def inspect(self, run_id, *, expected_digest=None):
        folder = self.folder(run_id)
        if not folder.exists():
            return Evidence("absent")
        request = read(folder / "request.json")
        bound = UsageBinding(**request["binding"])
        if expected_digest is not None and expected_digest != bound.input_digest:
            return Evidence("unknown")
        if not (folder / "receipt.json").exists():
            return Evidence("running")
        return encode_receipt(self.receipt(run_id), bound)[2]
