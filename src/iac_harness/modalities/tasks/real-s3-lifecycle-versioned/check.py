"""O bucket tem lifecycle ativo no bucket inteiro com: versões não correntes mantidas em até 10
(NewerNoncurrentVersions) e expurgadas após 30 dias, e limpeza de delete markers expirados.
Nenhuma regra ativa expira versões correntes (Expiration com Days ou Date); versionamento
continua Enabled e os dois objetos continuam existindo."""

import json
import sys

import boto3
from botocore.exceptions import ClientError

BUCKET = "e13-s3-lifecycle-931493484341"
KEYS = {"app/config.json", "app/data.csv"}


def _whole_bucket(rule: dict) -> bool:
    """Sem filtro por prefixo, tag ou tamanho (a regra vale para o bucket inteiro)."""
    flt = rule.get("Filter") or {}
    flat = {k: v for k, v in flt.items() if v not in ("", None, [], {})}
    return not flat and not rule.get("Prefix")


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    if BUCKET not in [b["Name"] for b in s3.list_buckets().get("Buckets", [])]:
        return False, {"bucket_exists": False}
    try:
        rules = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET).get("Rules", [])
    except ClientError as exc:
        rules = []
        error = exc.response["Error"]["Code"]
    else:
        error = None
    active = [r for r in rules if r.get("Status") == "Enabled"]
    keeps = any(
        _whole_bucket(r)
        and (r.get("NoncurrentVersionExpiration") or {}).get("NoncurrentDays") == 30
        and (r.get("NoncurrentVersionExpiration") or {}).get("NewerNoncurrentVersions") == 10
        for r in active
    )
    markers = any(
        _whole_bucket(r) and (r.get("Expiration") or {}).get("ExpiredObjectDeleteMarker") is True
        for r in active
    )
    expires_current = any(
        (r.get("Expiration") or {}).get("Days") or (r.get("Expiration") or {}).get("Date")
        for r in active
    )
    versioning = s3.get_bucket_versioning(Bucket=BUCKET).get("Status")
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])}
    details = {
        "keeps_10_noncurrent_30_days": keeps, "cleans_expired_markers": markers,
        "expires_current_versions": bool(expires_current), "versioning": versioning,
        "keys": sorted(keys), "rules": rules, "lifecycle_error": error,
    }  # fmt: skip
    ok = keeps and markers and not expires_current and versioning == "Enabled" and KEYS <= keys
    return ok, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
