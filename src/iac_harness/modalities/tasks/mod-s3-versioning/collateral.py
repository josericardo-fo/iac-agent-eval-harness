"""Retrato do efeito colateral: o que deve ficar igual depois de habilitar o versionamento no
bucket "mod-versioning-bucket".

Guarda a lista de buckets, a data de criação do bucket (recriado = data nova), a região, as tags,
a política e a criptografia. O versionamento (alvo) fica de fora. Configuração ausente vira o
código de erro da API (determinístico).

Limite: o bucket não tem ID; a recriação só aparece pela data de criação, que no Floci 2.1.0 tem
resolução de segundo (apagar e recriar no mesmo segundo não é detectado).
"""

import json
import sys

import boto3
from botocore.exceptions import ClientError

BUCKET = "mod-versioning-bucket"


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
    snap["location"] = _get(s3.get_bucket_location, "LocationConstraint", Bucket=BUCKET)
    snap["tags"] = _get(s3.get_bucket_tagging, "TagSet", Bucket=BUCKET)
    snap["policy"] = _get(s3.get_bucket_policy, "Policy", Bucket=BUCKET)
    snap["encryption"] = _get(
        s3.get_bucket_encryption, "ServerSideEncryptionConfiguration", Bucket=BUCKET
    )
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
