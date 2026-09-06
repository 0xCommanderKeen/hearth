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
    parser.add_argument(
        "command",
        choices=[
            "demo",
            "ownership-demo",
            "backup",
            "verify-backup",
            "restore",
            "export-state",
            "verify-state",
            "import-state",
            "diff-state",
            "upgrade-state",
            "show-resident",
            "save-resident",
            "show-memory",
            "save-memory",
        ],
    )
    parser.add_argument("--data", type=Path, default=Path(".hearth/demo"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--against", type=Path)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--resident")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade a supported older backup during isolated restore",
    )
    args = parser.parse_args()
    if args.upgrade and args.command != "restore":
        parser.error("--upgrade is only valid with restore")
    if args.command in {"show-memory", "save-memory"}:
        from hearth.memory import MAX_MEMORY, Memory

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
    if args.command == "ownership-demo":
        from hearth.rehearsal import execution_handoff

        print(json.dumps(execution_handoff(args.data), indent=2))
        return
    if args.command == "diff-state":
        from hearth.portable import MAX_EXPORT, compare

        if args.source is None or args.against is None:
            parser.error("diff-state requires --source and --against")
        try:
            with args.source.open("rb") as before, args.against.open("rb") as after:
                result = compare(
                    before.read(MAX_EXPORT + 1), after.read(MAX_EXPORT + 1), limit=args.limit
                )
        except (Refused, OSError) as error:
            parser.error(str(error))
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["equal"] else 1)
    if args.command in {"export-state", "verify-state", "import-state", "upgrade-state"}:
        from hearth.portable import MAX_EXPORT, export, import_state, upgrade_state, validate

        if args.source is None:
            parser.error(f"{args.command} requires --source")
        if args.command == "export-state":
            if args.destination is None:
                parser.error("export-state requires --destination")
            result = export(args.source, args.destination)
        else:
            with args.source.open("rb") as file:
                content = file.read(MAX_EXPORT + 1)
            if args.command in {"import-state", "upgrade-state"}:
                if args.destination is None:
                    parser.error(f"{args.command} requires --destination")
                operation = import_state if args.command == "import-state" else upgrade_state
                result = operation(content, args.destination)
            else:
                result = validate(content)
        print(json.dumps(result, indent=2))
        return
    if args.command != "demo":
        from hearth.backup import capture, restore, verify

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
            result = restore(args.source, args.destination, upgrade=args.upgrade)
        print(json.dumps(result, indent=2))
        return
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
