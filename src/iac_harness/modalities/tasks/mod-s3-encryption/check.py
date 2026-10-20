"""Bucket "mod-encryption-bucket" existe e tem criptografia padrão SSE-KMS (aws:kms) com a chave
gerenciada pela AWS (sem chave, alias/aws/s3, ARN do alias ou key-id/ARN da chave aws/s3) e Bucket
Key habilitado. Chave do cliente é rejeitada."""

import json
import sys

import boto3
from botocore.exceptions import ClientError

BUCKET = "mod-encryption-bucket"
AWS_MANAGED_ALIAS = "alias/aws/s3"


def _aws_managed_key(key_id: str | None, region: str) -> bool:
    if not key_id or key_id == AWS_MANAGED_ALIAS:
        return True
    if key_id.startswith("arn:aws:kms:") and key_id.endswith(":" + AWS_MANAGED_ALIAS):
        return True
    # key-id/ARN da chave aws/s3: resolve o alias; se a chamada falhar, rejeita
    try:
        meta = boto3.client("kms", region_name=region).describe_key(KeyId=AWS_MANAGED_ALIAS)
    except Exception:
        return False
    return key_id in (meta["KeyMetadata"]["KeyId"], meta["KeyMetadata"]["Arn"])


def _ok(rule: dict, region: str) -> bool:
    default = rule.get("ApplyServerSideEncryptionByDefault", {})
    return (
        default.get("SSEAlgorithm") == "aws:kms"
        and rule.get("BucketKeyEnabled") is True
        and _aws_managed_key(default.get("KMSMasterKeyID"), region)
    )


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if BUCKET not in names:
        return False, {"bucket_exists": False, "buckets": names}
    try:
        rules = s3.get_bucket_encryption(Bucket=BUCKET)["ServerSideEncryptionConfiguration"][
            "Rules"
        ]
    except ClientError as exc:  # sem configuração de criptografia
        return False, {"bucket_exists": True, "encryption": exc.response["Error"]["Code"]}
    return any(_ok(r, region) for r in rules), {"bucket_exists": True, "rules": rules}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
