"""A senha do banco vive num parâmetro SSM SecureString com a chave `alias/aws/ssm`; nenhum
parâmetro String/StringList a guarda; não há secret no Secrets Manager nem chave KMS própria.

Premissa: a conta não tem chaves KMS gerenciadas pelo cliente fora do estudo (o inventário do
reset as remove; chave em PendingDeletion não conta).
"""

import json
import sys

import boto3
from botocore.exceptions import ClientError

NAME = "/e13/mariadb/password"
PASSWORD = "Tr0ub4dor-e13-Pa55"
DEFAULT_KEY = "alias/aws/ssm"


def _parameters(ssm) -> list[dict]:
    return [
        p
        for page in ssm.get_paginator("describe_parameters").paginate()
        for p in page["Parameters"]
    ]


def _value(ssm, name: str) -> str | None:
    """Valor decifrado; None se não foi possível ler (sem permissão de decifrar, por exemplo)."""
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ClientError:
        return None


def _aws_managed(kms, key_id: str | None) -> bool:
    """A chave é a gerenciada pela AWS (o padrão do SSM) e não uma do cliente."""
    if key_id in (None, DEFAULT_KEY):
        return True
    return kms.describe_key(KeyId=key_id)["KeyMetadata"]["KeyManager"] == "AWS"


def main(region: str) -> tuple[bool, dict]:
    ssm = boto3.client("ssm", region_name=region)
    kms = boto3.client("kms", region_name=region)
    secure, plain = [], []
    for p in _parameters(ssm):
        value = _value(ssm, p["Name"])
        if p["Type"] == "SecureString":
            holds = value == PASSWORD if value is not None else p["Name"] == NAME
            if holds:
                secure.append({"name": p["Name"], "key": p.get("KeyId") or DEFAULT_KEY})
        elif value is not None and PASSWORD in value:
            plain.append(p["Name"])
    default_key = [s for s in secure if _aws_managed(kms, s["key"])]
    secrets = [
        s["Name"]
        for page in boto3.client("secretsmanager", region_name=region)
        .get_paginator("list_secrets")
        .paginate()
        for s in page["SecretList"]
    ]
    own_keys = []
    for page in kms.get_paginator("list_keys").paginate():
        for k in page["Keys"]:
            meta = kms.describe_key(KeyId=k["KeyId"])["KeyMetadata"]
            if meta["KeyManager"] == "CUSTOMER" and meta["KeyState"] in ("Enabled", "Disabled"):
                own_keys.append(k["KeyId"])
    details = {
        "securestring_with_password": secure, "uses_default_key": bool(default_key),
        "plaintext_parameters": plain, "secrets": secrets, "customer_keys": own_keys,
    }  # fmt: skip
    ok = bool(default_key) and not plain and not secrets and not own_keys
    return ok, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
