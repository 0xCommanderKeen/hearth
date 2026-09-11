"""Operator maintenance commands for a Hearth data directory."""

import argparse
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from hearth.residents.models import Declaration, Refused
from hearth.storage.database import Database
from hearth.work.service import Hearth


def credentials(data: Path) -> list[dict]:
    """Which residents hold a provider login of their own, and whether it still works.

    One line per resident per runtime kind, and each line says three things: whose
    login it is, which brain it is for, and whether that provider answers "logged in".
    Nothing else of the provider's answer is read -- it names the account, the plan and
    the organisation, and none of that is Hearth's to print -- and nothing ever reads
    the credential itself.

    `logged_in` is `null` where this command could not ask: no server is running here,
    so a provider whose pinned binary is not named in the environment cannot be
    started. Not knowing is said rather than rounded down to a "no", because a login
    nobody probed has not lapsed.

    The question is asked the way the *sessions* will be asked it, which on the
    container launcher is stricter than on this host: `HEARTH_SANDBOX` is read from the
    environment for the same reason the server reads it, so an operator running this on
    a burrow that sandboxes its runs is not told a Keychain-backed login works when
    every run on it will be held (`docs/sandbox.md`).
    """
    import os

    from hearth.integrations.interface import binary_environment, live_kinds, login_probe
    from hearth.integrations.launcher import CONTAINER
    from hearth.integrations.logins import seeded

    contained = os.environ.get("HEARTH_SANDBOX") == CONTAINER
    found = []
    for entry in seeded(data, live_kinds()):
        probe = login_probe(entry.kind)
        variable = binary_environment(entry.kind)
        named = os.environ.get(variable) if variable else None
        try:
            answer = (
                probe(Path(named) if named else None, entry.directory, contained=contained)
                if probe
                else None
            )
        except Refused:
            answer = None
        found.append(
            {
                "resident_id": entry.resident_id,
                "kind": entry.kind,
                "path": str(entry.directory),
                "logged_in": answer,
            }
        )
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "backup",
            "verify-backup",
            "restore",
            "show-resident",
            "save-resident",
            "show-memory",
            "save-memory",
            "export-resident",
            "import-resident",
            "credentials",
            "communications",
        ],
    )
    parser.add_argument("action", nargs="?", choices=["list", "inspect", "probe"])
    parser.add_argument(
        "--section",
        choices=["configuration", "conversations", "deliveries", "forwarding", "usage"],
        default="conversations",
    )
    parser.add_argument("--id", dest="identity")
    parser.add_argument("--route")
    parser.add_argument("--kind", choices=["reply", "announcement", "notification"])
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--after", default="")
    parser.add_argument("--before")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--data", type=Path, default=Path(".hearth"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--resident")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--name")
    parser.add_argument("--daily-limit", type=int)
    parser.add_argument("--command-id")
    args = parser.parse_args()
    if args.command == "communications":
        from hearth.channels.cli import communications

        try:
            print(json.dumps(communications(args), ensure_ascii=True, indent=2))
        except Refused as error:
            parser.error(error.code)
        return
    if args.command == "credentials":
        print(json.dumps(credentials(args.data), indent=2))
        return
    if args.command in {"export-resident", "import-resident"}:
        from hearth.management.authority import protected_paths
        from hearth.residents.bundle import Bundles, load_bundle_file

        # This command has no adapters open, so what it protects is the store's own
        # directory and the container runtime's socket; a login outside the data
        # directory is refused when the grant reaches a running Hearth.
        bundles = Bundles(Hearth(Database(args.data / "hearth.db")), protected_paths(args.data))
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
                required = {"name", "purpose", "daily_limit", "budget_timezone", "skill_text"}
                optional = {"memory_writable", "letters_accept", "runtime"}
                if (
                    not isinstance(values, dict)
                    or not required <= set(values)
                    or set(values) - required - optional
                ):
                    raise Refused("declaration_fields_invalid")
                if not optional <= set(values):
                    # An omitted capability, door or runtime keeps what stands. A
                    # `runtime` of null is the operator saying "the store's default",
                    # which is a different statement from not mentioning it at all.
                    with hearth.database.transaction() as db:
                        values.setdefault(
                            "memory_writable",
                            hearth.declared_memory_writable(db, args.resident),
                        )
                        values.setdefault(
                            "letters_accept",
                            hearth.declared_letters_accept(db, args.resident),
                        )
                        values.setdefault("runtime", hearth.declared_runtime(db, args.resident))
                declaration = Declaration(**values)
                resident = hearth.save_resident(
                    args.resident, declaration, expected_revision=args.expected_revision
                )
            print(json.dumps(asdict(resident), indent=2, ensure_ascii=True))
        except (Refused, OSError, TypeError, ValueError) as error:
            parser.error(str(error))
        return
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


if __name__ == "__main__":
    main()
