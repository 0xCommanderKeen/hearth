"""Scoped maintenance calls; every edit shares the ordinary resident writer."""

import json

from pydantic import BaseModel, ConfigDict, Field

from hearth.management.authority import digest
from hearth.management.bridge import authorize_managed_resident
from hearth.residents.maintenance import ConfigurationChange, LifecycleChange, Maintenance
from hearth.residents.models import Refused
from hearth.work.routines import ROUTINE_RESERVATION


class ResidentTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resident_id: str = Field(min_length=1, max_length=128)


class ConfigurationRead(ResidentTarget):
    offset: int = Field(default=0, ge=0)
    expected_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Configure(ResidentTarget):
    operation_id: str = Field(min_length=1, max_length=128)
    changes: ConfigurationChange


class ChangeLifecycle(ResidentTarget):
    operation_id: str = Field(min_length=1, max_length=128)
    change: LifecycleChange


MAINTENANCE_TOOLS = {
    "hearth_residents_configuration": (
        ConfigurationRead,
        "Read complete editable configuration as bounded JSON-text pages. Concatenate text in "
        "offset order, then parse JSON to recover all values and owning revisions. Start at "
        "offset 0; pass the returned digest as expected_digest with every next_offset until "
        "next_offset is null. If configuration_changed is refused, restart from offset 0. "
        "Inspect before editing; preserve concurrent human changes.",
    ),
    "hearth_residents_configure": (
        Configure,
        "Update selected configuration groups of a resident you manage in one operation. "
        "Supply each group's observed revision and the lifecycle revision. Omitted groups stay "
        "unchanged. Skills require assign_skills and routines require routines authority. "
        "Keep operation_id and payload for uncertain retries; a conflict requires reading again.",
    ),
    "hearth_residents_lifecycle": (
        ChangeLifecycle,
        "Pause, resume or archive a resident you manage using its observed lifecycle revision. "
        "Pause blocks future admissions/occurrences; admitted work may continue. Archive also "
        "blocks prelaunch dispatch and preserves history and unresolved reservations. Archive "
        "is final. Neither action cancels active execution or clears safety holds.",
    ),
}


def dispatch_maintenance(db, hearth, authority, tool: str, arguments: dict) -> dict:
    from hearth.management.tools import _existing_operation, _record_operation

    body = MAINTENANCE_TOOLS[tool][0].model_validate(arguments)
    action = "manage_lifecycle" if isinstance(body, ChangeLifecycle) else "update_residents"
    authorize_managed_resident(db, authority, body.resident_id, action)
    maintenance = Maintenance(hearth)
    if not isinstance(body, Configure | ChangeLifecycle):
        configuration = maintenance.configuration_in_transaction(db, body.resident_id)
        current_digest = digest(configuration)
        if body.offset and body.expected_digest is None:
            raise Refused("configuration_digest_required")
        if body.expected_digest is not None and body.expected_digest != current_digest:
            raise Refused("configuration_changed")
        serialized = json.dumps(configuration, sort_keys=True, ensure_ascii=False)
        if body.offset > len(serialized):
            raise Refused("configuration_offset_invalid")
        end = min(body.offset + 32000, len(serialized))
        return {
            "resident_id": body.resident_id,
            "digest": current_digest,
            "encoding": "json",
            "offset": body.offset,
            "text": serialized[body.offset : end],
            "next_offset": end if end < len(serialized) else None,
        }
    if isinstance(body, Configure):
        changes = body.changes
        grant = authority["grant"]
        if (
            changes.declaration is not None
            and changes.declaration.daily_limit > grant["max_daily_limit"]
        ):
            raise Refused("management_resident_budget_limit")
        # Only an escalation needs the grant: echoing back a capability the resident
        # already has is the ordinary preserve-what-you-read edit, not a new grant.
        if (
            changes.declaration is not None
            and changes.declaration.memory_writable
            and "writable_memory" not in grant["capabilities"]
            and not hearth.declared_memory_writable(db, body.resident_id)
        ):
            raise Refused("management_memory_not_permitted")
        if changes.inputs is not None and any(
            item.input_set_id not in grant["input_set_ids"] for item in changes.inputs.input_sets
        ):
            raise Refused("management_input_not_permitted")
        if changes.skills is not None and "assign_skills" not in grant["capabilities"]:
            raise Refused("management_skill_assignment_not_permitted")
        if changes.routines:
            if "routines" not in grant["capabilities"]:
                raise Refused("management_routine_not_permitted")
            if (
                any(item.enabled for item in changes.routines)
                and ROUTINE_RESERVATION > grant["max_reserve"]
            ):
                raise Refused("management_reservation_limit")
    payload = digest([tool, arguments])
    previous = _existing_operation(db, authority, body.operation_id, payload)
    if previous:
        return previous
    command_id = "maintenance:" + digest([authority["actor"], body.operation_id])
    if isinstance(body, Configure):
        result = maintenance.configure_in_transaction(
            db,
            command_id,
            body.resident_id,
            body.changes,
            actor=authority["actor"],
            originating_run_id=authority["run_id"],
        )
    else:
        result = maintenance.change_lifecycle_in_transaction(
            db,
            command_id,
            body.resident_id,
            body.change,
            actor=authority["actor"],
            originating_run_id=authority["run_id"],
        )
    return _record_operation(
        db,
        hearth,
        authority,
        body.operation_id,
        payload,
        result
        | {
            "status": result.get("state", "configured"),
            "resident_link": "/#residents/" + body.resident_id,
        },
    )
