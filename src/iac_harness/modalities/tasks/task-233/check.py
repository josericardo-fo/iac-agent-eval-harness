"""Alguma tabela DynamoDB da região tem cifragem em repouso (padrão da AWS ou KMS explícito).

Sem SSEDescription a tabela usa a chave da AWS (cifrada); só SSE em estado diferente de
ENABLED/ENABLING (ex.: DISABLED) reprova.
"""

import json
import sys

import boto3

OK_STATUS = (None, "ENABLED", "ENABLING")


def main(region: str) -> tuple[bool, dict]:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = ddb.list_tables().get("TableNames", [])
    sse = {}
    for name in tables:
        desc = ddb.describe_table(TableName=name)["Table"].get("SSEDescription") or {}
        sse[name] = {"status": desc.get("Status"), "type": desc.get("SSEType")}
    return any(s["status"] in OK_STATUS for s in sse.values()), {"tables": sse}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
