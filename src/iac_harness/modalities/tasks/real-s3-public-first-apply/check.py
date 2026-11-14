"""O bucket "e13-public-931493484341" existe, o bloqueio de acesso público permite a política e
a política dá s3:GetObject anônimo nos objetos do bucket."""

import json
import sys

import boto3
from botocore.exceptions import ClientError

BUCKET = "e13-public-931493484341"


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _grants_public_read(policy: dict) -> bool:
    """Algum Allow para qualquer principal ("*" ou {"AWS": "*"}) com s3:GetObject nos objetos."""
    for stmt in _as_list(policy.get("Statement", [])):
        principal = stmt.get("Principal")
        anyone = principal == "*" or (
            isinstance(principal, dict) and "*" in _as_list(principal.get("AWS"))
        )
        actions = {a.lower() for a in _as_list(stmt.get("Action"))}
        resources = _as_list(stmt.get("Resource"))
        if (
            stmt.get("Effect") == "Allow"
            and anyone
            and actions & {"s3:getobject", "s3:*", "*"}
            and any(r in (f"arn:aws:s3:::{BUCKET}/*", "*") for r in resources)
        ):
            return True
    return False


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    if BUCKET not in [b["Name"] for b in s3.list_buckets().get("Buckets", [])]:
        return False, {"bucket_exists": False}
    try:
        block = s3.get_public_access_block(Bucket=BUCKET)["PublicAccessBlockConfiguration"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchPublicAccessBlockConfiguration":
            raise
        block = {}  # sem configuração: nada bloqueia a política
    allows_policy = not block.get("BlockPublicPolicy") and not block.get("RestrictPublicBuckets")
    try:
        policy = json.loads(s3.get_bucket_policy(Bucket=BUCKET)["Policy"])
    except ClientError:
        policy = {}
    public_read = _grants_public_read(policy)
    details = {
        "bucket_exists": True, "public_access_block": block,
        "block_allows_public_policy": allows_policy, "policy_grants_public_read": public_read,
    }  # fmt: skip
    try:  # só informativo: o Floci pode não implementar
        status = s3.get_bucket_policy_status(Bucket=BUCKET)["PolicyStatus"]
        details["policy_status_is_public"] = status.get("IsPublic")
    except ClientError:
        pass
    return allows_policy and public_read, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
