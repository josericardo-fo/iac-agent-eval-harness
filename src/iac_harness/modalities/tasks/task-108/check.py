"""Bucket "sample" existe e tem versionamento Enabled."""

import json
import sys

import boto3

BUCKET = "sample"


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if BUCKET not in names:
        return False, {"bucket_exists": False, "buckets": names}
    status = s3.get_bucket_versioning(Bucket=BUCKET).get("Status")
    return status == "Enabled", {"bucket_exists": True, "versioning": status}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
