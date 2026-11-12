"""Retrato do efeito colateral: o que deve ficar igual depois de pôr a tag Environment no segredo
"mod-tagged-secret".

Guarda a lista de segredos (inclusive os agendados para remoção) e, no segredo alvo, ARN,
CreatedDate (recriado = valores novos), descrição, chave KMS, agendamento de remoção, as tags
menos Environment (alvo) e o valor atual (sem valor, o código de erro da API). Fora: datas de
último acesso/alteração (mudam com a própria tag).
"""

import json
import sys

import boto3
from botocore.exceptions import ClientError

SECRET = "mod-tagged-secret"
TARGET_TAG = "Environment"


def main(region: str) -> dict:
    sm = boto3.client("secretsmanager", region_name=region)
    pages = sm.get_paginator("list_secrets").paginate(IncludePlannedDeletion=True)
    secrets = [s for page in pages for s in page.get("SecretList", [])]
    snap: dict = {"secrets": sorted([s.get("Name"), s.get("ARN")] for s in secrets)}
    if not any(s.get("Name") == SECRET for s in secrets):
        return snap
    d = sm.describe_secret(SecretId=SECRET)
    try:
        value = sm.get_secret_value(SecretId=SECRET).get("SecretString")
    except ClientError as exc:
        value = {"error": exc.response["Error"]["Code"]}
    snap["secret"] = {
        "arn": d.get("ARN"),
        "created": d.get("CreatedDate"),
        "description": d.get("Description"),
        "kms_key": d.get("KmsKeyId"),
        "deleted": d.get("DeletedDate"),
        "tags": sorted((t["Key"], t["Value"]) for t in d.get("Tags", []) if t["Key"] != TARGET_TAG),
        "value": value,
    }
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
