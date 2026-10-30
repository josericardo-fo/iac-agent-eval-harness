"""Bucket "del-logs-old" não existe; "del-logs-current" continua com o seu objeto."""

import json
import sys

import boto3

OBJECTS = {"del-logs-old": "app/2024-01.log", "del-logs-current": "app/2025-01.log"}


def _state(region: str) -> dict:
    """Bucket → lista de chaves (None se o bucket não existe)."""
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    return {
        b: (
            [o["Key"] for o in s3.list_objects_v2(Bucket=b).get("Contents", [])]
            if b in names
            else None
        )
        for b in OBJECTS
    }


def main(region: str) -> tuple[bool, dict]:
    state = _state(region)
    removed = state["del-logs-old"] is None
    kept = (
        state["del-logs-current"] is not None
        and OBJECTS["del-logs-current"] in state["del-logs-current"]
    )
    return removed and kept, {"removed": removed, "kept": kept, "buckets": state}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
