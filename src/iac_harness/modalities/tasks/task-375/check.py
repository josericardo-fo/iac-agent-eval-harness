"""Alguma instância Lightsail da região tem 2 ou mais discos anexados ("multiple")."""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    ls = boto3.client("lightsail", region_name=region)
    attached = {i["name"]: [] for i in ls.get_instances().get("instances", [])}
    for d in ls.get_disks().get("disks", []):
        if d.get("isAttached") and d.get("attachedTo") in attached:
            attached[d["attachedTo"]].append(d["name"])
    return any(len(v) >= 2 for v in attached.values()), {"attached_disks": attached}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
