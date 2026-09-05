"""Run an explicit, local-only mock demonstration: python -m hearth demo."""

import argparse
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from hearth.artifacts import Artifacts
from hearth.core import Hearth
from hearth.database import Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.runtime import MockRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo"])
    parser.add_argument("--data", type=Path, default=Path(".hearth/demo"))
    args = parser.parse_args()
    db = Database(args.data / "hearth.db")
    db.initialize()
    hearth = Hearth(db)
    try:
        hearth.resident("reader")
    except Refused as error:
        if error.code != "resident_not_found":
            raise
        hearth.save_resident(
            "reader",
            Declaration("Reader", "Summarize synthetic notes.", 1_000_000),
            expected_revision=0,
        )
    receipt = hearth.submit(
        str(uuid.uuid4()),
        "reader",
        "Produce a simulated daily summary.",
        expires_at=int(time.time()) + 3600,
    )
    run = hearth.admit(receipt.task_id, reserve=10_000)
    execution = Execution(hearth, Artifacts(args.data / "artifacts"))
    executor = Executor(execution, MockRuntime(args.data / "mock-runtime"))
    executor.step()
    result = hearth.run(run.id)
    print(
        json.dumps(
            {
                "simulated": True,
                "task": asdict(hearth.task(receipt.task_id)),
                "status": result.status,
                "synthetic_cost_microdollars": result.actual_cost,
                "artifact_id": result.artifact_id,
            },
            indent=2,
        )
    )
    if result.artifact_id:
        print(execution.artifact(result.artifact_id)[1])


if __name__ == "__main__":
    main()
