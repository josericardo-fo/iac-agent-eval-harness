"""Retrato do efeito colateral: o que deve ficar igual depois de proteger a senha do banco.

Guarda os secrets do Secrets Manager, as chaves KMS do cliente e os nomes dos parâmetros SSM
exceto o da senha (/e13/mariadb/password), que é o alvo.
"""

import json
import sys

import boto3

TARGET = "/e13/mariadb/password"


def main(region: str) -> dict:
    ssm = boto3.client("ssm", region_name=region)
    kms = boto3.client("kms", region_name=region)
    names = sorted(
        p["Name"]
        for page in ssm.get_paginator("describe_parameters").paginate()
        for p in page["Parameters"]
        if p["Name"] != TARGET
    )
    secrets = sorted(
        s["Name"]
        for page in boto3.client("secretsmanager", region_name=region)
        .get_paginator("list_secrets")
        .paginate()
        for s in page["SecretList"]
    )
    keys = []
    for page in kms.get_paginator("list_keys").paginate():
        for k in page["Keys"]:
            meta = kms.describe_key(KeyId=k["KeyId"])["KeyMetadata"]
            if meta["KeyManager"] == "CUSTOMER":
                keys.append([k["KeyId"], meta["KeyState"]])
    return {"ssm_parameters": names, "secrets": secrets, "customer_keys": sorted(keys)}


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
