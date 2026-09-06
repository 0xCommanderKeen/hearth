"""Fresh synthetic systems exercise the execution fence without activating imports."""

import time
from pathlib import Path

from hearth.artifacts import Artifacts
from hearth.core import Hearth
from hearth.database import Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.ownership import ExecutionGuard, Ownership
from hearth.runtime import MockRuntime


def execution_handoff(destination: Path) -> dict:
    try:
        destination.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        raise Refused("rehearsal_destination_exists") from None
    registry = Ownership(destination / "shared/ownership.db")
    registry.initialize()
    systems = []
    for name in ("source", "target"):
        root = destination / name
        database = Database(root / "hearth.db")
        database.initialize()
        hearth = Hearth(database)
        hearth.save_resident(
            "reader", Declaration("Reader", "Synthetic handoff", 10000), expected_revision=0
        )
        worker = Executor(
            Execution(hearth, Artifacts(root / "artifacts")),
            MockRuntime(root / "mock-runtime", scenario="hold" if name == "source" else "success"),
            guard=ExecutionGuard(registry, name, hearth),
        )
        systems.append((hearth, worker))
    registry.register("reader", "source")
    source, old = systems[0]
    target, new = systems[1]

    def admit(hearth, key):
        task = hearth.submit(
            key, "reader", "Synthetic handoff summary", expires_at=int(time.time()) + 600
        )
        return hearth.admit(task.task_id, reserve=3000)

    running = admit(source, "source-work")
    old.step()
    try:
        registry.transfer(
            "handoff", "reader", source="source", target="target", expected_revision=1
        )
    except Refused as error:
        if error.code != "execution_claim_unsettled":
            raise
    else:
        raise RuntimeError("Rehearsal failed: active work allowed transfer")
    old.execution.cancel(running.id)
    old.step()
    receipt = registry.transfer(
        "handoff", "reader", source="source", target="target", expected_revision=1
    )
    completed = admit(target, "target-work")
    new.step()
    stale = admit(source, "stale-source-work")
    old.step()
    if (
        target.run(completed.id).status != "succeeded"
        or old.runtime.inspect(stale.id).status != "absent"
    ):
        raise RuntimeError("Rehearsal failed: execution fence violated")
    return {
        "simulated": True,
        "transfer": receipt,
        "source_run": source.run(running.id).status,
        "target_run": target.run(completed.id).status,
        "stale_source_run": source.run(stale.id).status,
        "owner": registry.state("reader"),
        "active_transfer_refused": True,
        "scope": "execution fence only; no resident data or effect authority migrated",
    }
