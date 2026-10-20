"""Bucket "wellcomecollection-platform-infra": ACL privada, versionamento Enabled e lifecycle com
expiração de "tmp/" em 30 dias, transição para STANDARD_IA em 30 dias (corrente ou não corrente)
e expiração de versões não correntes em 90 dias (regras Enabled).
"""

import json
import sys

import boto3

BUCKET = "wellcomecollection-platform-infra"


def _prefix(rule: dict) -> str | None:
    flt = rule.get("Filter", {})
    return flt.get("Prefix", flt.get("And", {}).get("Prefix", rule.get("Prefix")))


def _lifecycle_ok(rules: list[dict]) -> dict:
    enabled = [r for r in rules if r.get("Status") == "Enabled"]
    tmp = any(_prefix(r) == "tmp/" and r.get("Expiration", {}).get("Days") == 30 for r in enabled)
    transition = any(
        t.get("StorageClass") == "STANDARD_IA"
        and (t.get("Days") == 30 or t.get("NoncurrentDays") == 30)
        for r in enabled
        for t in r.get("Transitions", []) + r.get("NoncurrentVersionTransitions", [])
    )
    noncurrent = any(
        r.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays") == 90 for r in enabled
    )
    return {
        "tmp_expiration_30": tmp,
        "standard_ia_30": transition,
        "noncurrent_expiration_90": noncurrent,
    }


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if BUCKET not in names:
        return False, {"bucket_exists": False, "buckets": names}
    acl = s3.get_bucket_acl(Bucket=BUCKET)
    owner = acl.get("Owner", {}).get("ID")
    extra = [g for g in acl.get("Grants", []) if g.get("Grantee", {}).get("ID") != owner]
    versioning = s3.get_bucket_versioning(Bucket=BUCKET).get("Status")
    try:
        rules = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET).get("Rules", [])
    except s3.exceptions.ClientError as exc:
        rules = []
        if exc.response.get("Error", {}).get("Code") != "NoSuchLifecycleConfiguration":
            raise
    lifecycle = _lifecycle_ok(rules)
    details = {
        "bucket_exists": True,
        "acl_extra_grants": extra,
        "versioning": versioning,
        "lifecycle": lifecycle,
        "rules": rules,
    }
    return not extra and versioning == "Enabled" and all(lifecycle.values()), details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
