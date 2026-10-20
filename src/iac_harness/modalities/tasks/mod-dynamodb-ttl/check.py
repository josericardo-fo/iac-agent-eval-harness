"""Tabela "mod-ttl-table" existe e tem TTL habilitado no atributo "expires_at"."""

import json
import sys

import boto3

TABLE = "mod-ttl-table"
ATTRIBUTE = "expires_at"


def main(region: str) -> tuple[bool, dict]:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = ddb.list_tables().get("TableNames", [])
    if TABLE not in tables:
        return False, {"table_exists": False, "tables": tables}
    desc = ddb.describe_time_to_live(TableName=TABLE)["TimeToLiveDescription"]
    status, attribute = desc.get("TimeToLiveStatus"), desc.get("AttributeName")
    passed = status == "ENABLED" and attribute == ATTRIBUTE
    return passed, {"table_exists": True, "ttl_status": status, "ttl_attribute": attribute}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
