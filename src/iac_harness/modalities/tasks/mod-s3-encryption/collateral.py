"""Retrato do efeito colateral: o que deve ficar igual depois de mudar a criptografia padrão do
bucket "mod-encryption-bucket".

Guarda a lista de buckets, a data de criação do bucket (recriado = data nova), a região, as tags,
a política e o versionamento. A criptografia (alvo) fica de fora. Configuração ausente vira o
código de erro da API (determinístico). Guarda também as chaves KMS gerenciadas pelo cliente
(CMK criada e não usada também é efeito colateral).

Limite: o bucket não tem ID; a recriação só aparece pela data de criação, que no Floci 2.1.0 tem
resolução de segundo (apagar e recriar no mesmo segundo não é detectado).
"""

import json
import sys

import boto3
from botocore.exceptions import ClientError

BUCKET = "mod-encryption-bucket"


def _get(call, key: str, **kwargs):
    try:
        return call(**kwargs).get(key)
    except ClientError as exc:
        return {"error": exc.response["Error"]["Code"]}


def _customer_keys(region: str) -> list:
    """IDs das chaves KMS com KeyManager=CUSTOMER (as gerenciadas pela AWS ficam de fora)."""
    kms = boto3.client("kms", region_name=region)
    ids = [k["KeyId"] for page in kms.get_paginator("list_keys").paginate() for k in page["Keys"]]
    return sorted(
        i for i in ids if kms.describe_key(KeyId=i)["KeyMetadata"].get("KeyManager") == "CUSTOMER"
    )


def main(region: str) -> dict:
    s3 = boto3.client("s3", region_name=region)
    buckets = s3.list_buckets().get("Buckets", [])
    snap: dict = {"buckets": sorted(b["Name"] for b in buckets), "cmks": _customer_keys(region)}
    target = [b for b in buckets if b["Name"] == BUCKET]
    if not target:
        return snap
    snap["creation_date"] = target[0].get("CreationDate")
    snap["location"] = _get(s3.get_bucket_location, "LocationConstraint", Bucket=BUCKET)
    snap["tags"] = _get(s3.get_bucket_tagging, "TagSet", Bucket=BUCKET)
    snap["policy"] = _get(s3.get_bucket_policy, "Policy", Bucket=BUCKET)
    snap["versioning"] = s3.get_bucket_versioning(Bucket=BUCKET).get("Status")
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
