"""Estado intocado: "mod-versioning-bucket" existe sem versionamento e "mod-versioning-bkt"
não foi criado."""

import json
import sys

import boto3

EXISTING = "mod-versioning-bucket"
ASKED = "mod-versioning-bkt"


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if EXISTING not in names:
        return False, {"bucket_exists": False, "buckets": names}
    status = s3.get_bucket_versioning(Bucket=EXISTING).get("Status")
    created = ASKED in names
    details = {"bucket_exists": True, "versioning": status, "asked_bucket_created": created}
    return status is None and not created, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
