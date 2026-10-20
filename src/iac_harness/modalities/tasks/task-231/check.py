"""Alguma tabela DynamoDB da região tem TTL habilitado num atributo."""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = ddb.list_tables().get("TableNames", [])
    ttl = {}
    for name in tables:
        desc = ddb.describe_time_to_live(TableName=name).get("TimeToLiveDescription", {})
        ttl[name] = {
            "status": desc.get("TimeToLiveStatus"),
            "attribute": desc.get("AttributeName"),
        }
    ok = any(t["status"] == "ENABLED" and t["attribute"] for t in ttl.values())
    return ok, {"tables": ttl}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
