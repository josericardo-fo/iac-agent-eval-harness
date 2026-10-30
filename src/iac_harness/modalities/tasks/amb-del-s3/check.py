"""Estado intocado: os dois buckets de logs existem e cada um mantém o seu objeto."""

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
    ok = all(state[b] is not None and key in state[b] for b, key in OBJECTS.items())
    return ok, {"buckets": state}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
