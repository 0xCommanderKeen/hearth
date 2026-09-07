"""Run an explicit, local-only mock demonstration: python -m hearth demo."""

import argparse
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from hearth.execution.lifecycle import Execution, Executor
from hearth.integrations.mock.inline import MockRuntime
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "demo",
            "backup",
            "verify-backup",
            "restore",
            "show-resident",
            "save-resident",
            "show-memory",
            "save-memory",
            "export-resident",
            "import-resident",
        ],
    )
    parser.add_argument("--data", type=Path, default=Path(".hearth/demo"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--resident")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--name")
    parser.add_argument("--daily-limit", type=int)
    parser.add_argument("--command-id")
    args = parser.parse_args()
    if args.command in {"export-resident", "import-resident"}:
        from hearth.residents.bundle import Bundles, load_bundle_file

        bundles = Bundles(Hearth(Database(args.data / "hearth.db")))
        try:
            if args.command == "export-resident":
                if args.resident is None or args.destination is None:
                    parser.error("export-resident requires --resident and --destination")
                result = bundles.export(args.resident)
                args.destination.write_text(
                    json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
                )
            else:
                if args.source is None:
                    parser.error("import-resident requires --source")
                overrides = {
                    key: value
                    for key, value in (("name", args.name), ("daily_limit", args.daily_limit))
                    if value is not None
                }
                request = {"bundle": load_bundle_file(args.source)}
                if overrides:
                    request["overrides"] = overrides
                result = bundles.import_(args.command_id or str(uuid.uuid4()), request)
            print(json.dumps(result, indent=2, ensure_ascii=True))
        except (Refused, OSError, ValueError) as error:
            parser.error(str(error))
        return
    if args.command in {"show-memory", "save-memory"}:
        from hearth.residents.memory import MAX_MEMORY, Memory

        if args.resident is None:
            parser.error("memory commands require --resident")
        memory = Memory(Hearth(Database(args.data / "hearth.db")))
        try:
            if args.command == "show-memory":
                result = memory.read(args.resident, revision=args.revision)
            else:
                if args.source is None or args.expected_revision is None:
                    parser.error("save-memory requires --source and --expected-revision")
                with args.source.open("rb") as file:
                    data = file.read(MAX_MEMORY + 1)
                if len(data) > MAX_MEMORY:
                    raise Refused("memory_too_large")
                result = memory.save(
                    args.resident, data.decode("utf-8"), expected_revision=args.expected_revision
                )
            print(json.dumps(result, indent=2, ensure_ascii=True))
        except (Refused, OSError, ValueError) as error:
            parser.error(str(error))
        return
    if args.command in {"show-resident", "save-resident"}:
        if args.resident is None:
            parser.error("resident commands require --resident")
        hearth = Hearth(Database(args.data / "hearth.db"))
        try:
            if args.command == "show-resident":
                resident = hearth.resident(args.resident, revision=args.revision)
            else:
                if args.source is None or args.expected_revision is None:
                    parser.error("save-resident requires --source and --expected-revision")
                with args.source.open("rb") as file:
                    content = file.read(262_145)
                if len(content) > 262_144:
                    raise Refused("declaration_file_too_large")
                values = json.loads(content)
                if not isinstance(values, dict) or set(values) != {
                    "name",
                    "purpose",
                    "daily_limit",
                    "budget_timezone",
                    "skill_text",
                }:
                    raise Refused("declaration_fields_invalid")
                declaration = Declaration(**values)
                resident = hearth.save_resident(
                    args.resident, declaration, expected_revision=args.expected_revision
                )
            print(json.dumps(asdict(resident), indent=2, ensure_ascii=True))
        except (Refused, OSError, TypeError, ValueError) as error:
            parser.error(str(error))
        return
    if args.command != "demo":
        from hearth.storage.backup import capture, restore, verify

        if args.command == "backup":
            if args.destination is None:
                parser.error("backup requires --destination")
            result = capture(args.data, args.destination)
        elif args.command == "verify-backup":
            if args.source is None:
                parser.error("verify-backup requires --source")
            result = verify(args.source)
        else:
            if args.source is None or args.destination is None:
                parser.error("restore requires --source and --destination")
            result = restore(args.source, args.destination)
        print(json.dumps(result, indent=2))
        return
    db = Database(args.data / "hearth.db")
    db.initialize(runtime_kind="inline_mock")
    hearth = Hearth(db)
    from hearth.inputs.demo import seed_reader

    seed_reader(hearth)
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
