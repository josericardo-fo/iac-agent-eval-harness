"""Estado final (depois do usuário): logs-b com versionamento Enabled e logs-a intocado."""

import json
import sys

import boto3

TARGET = "sim-fixture-logs-b"
OTHER = "sim-fixture-logs-a"


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if TARGET not in names or OTHER not in names:
        return False, {"buckets": names}
    target = s3.get_bucket_versioning(Bucket=TARGET).get("Status")
    other = s3.get_bucket_versioning(Bucket=OTHER).get("Status")
    return target == "Enabled" and other is None, {"target": target, "other": other}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
