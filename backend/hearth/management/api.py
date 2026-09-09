"""Operator-only grant and management bootstrap routes."""

import json

from fastapi import FastAPI

from hearth.inputs.catalog import list_inputs
from hearth.management.authority import GrantPut, Management, read_grant
from hearth.management.bootstrap import bootstrap
from hearth.residents.provisioning import execution_profiles
from hearth.work.service import Hearth


def mount_management(app: FastAPI, hearth: Hearth) -> None:
    management = Management(hearth)

    @app.get("/api/management")
    def catalog():
        with hearth.database.transaction() as db:
            residents = [
                {"id": row["id"], "name": row["name"], "grant": read_grant(db, row["id"])}
                for row in db.execute(
                    "SELECT r.id,d.name FROM residents r JOIN declarations d "
                    "ON d.resident_id=r.id AND d.revision=r.revision ORDER BY d.name"
                )
            ]
            return {
                "residents": residents,
                # Named as well as identified: an operator grants a runtime by its own
                # name, and only the registry knows what that name is.
                "profiles": execution_profiles(db),
                "input_sets": [
                    {key: item[key] for key in ("input_set_id", "name", "revision", "synthetic")}
                    for item in list_inputs(db)
                ],
                "operations": [
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT receipt FROM management_operations ORDER BY rowid DESC LIMIT 30"
                    )
                ],
            }

    @app.post("/api/management/bootstrap")
    def setup():
        return bootstrap(hearth)

    @app.get("/api/residents/{resident_id}/management")
    def read(resident_id: str):
        return management.read(resident_id)

    @app.put("/api/residents/{resident_id}/management")
    def save(resident_id: str, body: GrantPut):
        return management.save(resident_id, body.model_dump())
