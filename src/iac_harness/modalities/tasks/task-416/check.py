"""Alguma instância RDS da região usa storage io1.

"basic" não é verificável: vale qualquer engine/classe. "creating" conta (o enunciado só pede a
criação); instâncias sendo apagadas ou com falha não contam.
"""

import json
import sys

import boto3

BAD_STATUS = ("deleting", "failed")


def main(region: str) -> tuple[bool, dict]:
    rds = boto3.client("rds", region_name=region)
    dbs = {
        db["DBInstanceIdentifier"]: {
            "status": db.get("DBInstanceStatus"),
            "storage_type": db.get("StorageType"),
            "iops": db.get("Iops"),
            "engine": db.get("Engine"),
        }
        for page in rds.get_paginator("describe_db_instances").paginate()
        for db in page.get("DBInstances", [])
    }
    ok = any(d["storage_type"] == "io1" and d["status"] not in BAD_STATUS for d in dbs.values())
    return ok, {"db_instances": dbs}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
