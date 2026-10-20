"""Bucket existe com bloqueio de ACLs públicas, policies públicas e buckets públicos.

O enunciado pede BlockPublicAcls, BlockPublicPolicy e RestrictPublicBuckets; IgnorePublicAcls
não é pedido e só aparece em details.
"""

import json
import sys

import boto3

BUCKET = "pike-680235478471"
REQUIRED = ("BlockPublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if BUCKET not in names:
        return False, {"bucket_exists": False, "buckets": names}
    try:
        cfg = s3.get_public_access_block(Bucket=BUCKET)["PublicAccessBlockConfiguration"]
    except s3.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        return False, {"bucket_exists": True, "public_access_block": None, "error_code": code}
    return all(cfg.get(k) is True for k in REQUIRED), {
        "bucket_exists": True,
        "public_access_block": cfg,
    }


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
