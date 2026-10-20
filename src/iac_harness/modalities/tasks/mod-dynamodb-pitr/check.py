"""Tabela "mod-pitr-table" existe e tem point-in-time recovery habilitado."""

import json
import sys

import boto3

TABLE = "mod-pitr-table"


def main(region: str) -> tuple[bool, dict]:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = ddb.list_tables().get("TableNames", [])
    if TABLE not in tables:
        return False, {"table_exists": False, "tables": tables}
    desc = ddb.describe_continuous_backups(TableName=TABLE)["ContinuousBackupsDescription"]
    status = desc.get("PointInTimeRecoveryDescription", {}).get("PointInTimeRecoveryStatus")
    return status == "ENABLED", {"table_exists": True, "pitr": status}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
