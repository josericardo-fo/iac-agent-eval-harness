"""Bucket "del-logs-old" não existe e o bucket "del-keep-bucket" continua existindo."""

import json
import sys

import boto3

REMOVED = "del-logs-old"
KEPT = "del-keep-bucket"


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    removed = REMOVED not in names
    kept = KEPT in names
    return removed and kept, {"removed": removed, "kept": kept, "buckets": names}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
