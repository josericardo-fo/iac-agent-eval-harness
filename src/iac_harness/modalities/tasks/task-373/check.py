"""Algum disco Lightsail da região está anexado a uma instância Lightsail existente."""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    ls = boto3.client("lightsail", region_name=region)
    instances = [i["name"] for i in ls.get_instances().get("instances", [])]
    disks = {
        d["name"]: {"attached": d.get("isAttached"), "attached_to": d.get("attachedTo")}
        for d in ls.get_disks().get("disks", [])
    }
    ok = any(d["attached"] and d["attached_to"] in instances for d in disks.values())
    return ok, {"instances": instances, "disks": disks}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
