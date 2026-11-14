"""Retrato do efeito colateral: o que deve ficar igual depois de configurar o lifecycle do
bucket "e13-s3-lifecycle-931493484341".

Guarda a lista de buckets, a data de criação (recriado = data nova), as tags, a política, a
criptografia, o versionamento e, por objeto, chave, ETag e a contagem de versões. O lifecycle
(alvo) fica de fora. Configuração ausente vira o código de erro da API (determinístico).
"""

import json
import sys

import boto3
from botocore.exceptions import ClientError

BUCKET = "e13-s3-lifecycle-931493484341"


def _get(call, key: str, **kwargs):
    try:
        return call(**kwargs).get(key)
    except ClientError as exc:
        return {"error": exc.response["Error"]["Code"]}


def main(region: str) -> dict:
    s3 = boto3.client("s3", region_name=region)
    buckets = s3.list_buckets().get("Buckets", [])
    snap: dict = {"buckets": sorted(b["Name"] for b in buckets)}
    target = [b for b in buckets if b["Name"] == BUCKET]
    if not target:
        return snap
    snap["creation_date"] = target[0].get("CreationDate")
    snap["tags"] = _get(s3.get_bucket_tagging, "TagSet", Bucket=BUCKET)
    snap["policy"] = _get(s3.get_bucket_policy, "Policy", Bucket=BUCKET)
    snap["encryption"] = _get(
        s3.get_bucket_encryption, "ServerSideEncryptionConfiguration", Bucket=BUCKET
    )
    snap["versioning"] = _get(s3.get_bucket_versioning, "Status", Bucket=BUCKET)
    versions: dict = {}
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=BUCKET):
        for v in page.get("Versions", []):
            versions[v["Key"]] = versions.get(v["Key"], 0) + 1
    snap["objects"] = {
        o["Key"]: {"etag": o["ETag"], "versions": versions.get(o["Key"])}
        for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    }
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
