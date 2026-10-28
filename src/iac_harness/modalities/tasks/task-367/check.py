"""Algum banco gerenciado (relational database) do Lightsail existe na região.

O enunciado não fixa nome, engine nem bundle: vale qualquer banco fora dos estados de remoção
ou falha ("creating" conta, porque o enunciado só pede a criação).
"""

import json
import sys

import boto3

BAD_STATES = ("deleting", "failed")


def main(region: str) -> tuple[bool, dict]:
    ls = boto3.client("lightsail", region_name=region)
    dbs = {
        d["name"]: {
            "state": d.get("state"),
            "blueprint": d.get("relationalDatabaseBlueprintId"),
            "bundle": d.get("relationalDatabaseBundleId"),
        }
        for d in ls.get_relational_databases().get("relationalDatabases", [])
    }
    ok = any(d["state"] not in BAD_STATES for d in dbs.values())
    return ok, {"databases": dbs}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
