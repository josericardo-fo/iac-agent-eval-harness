"""Alguma tabela DynamoDB da região tem point-in-time recovery habilitado."""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = ddb.list_tables().get("TableNames", [])
    pitr = {}
    for name in tables:
        desc = ddb.describe_continuous_backups(TableName=name)["ContinuousBackupsDescription"]
        pitr[name] = desc.get("PointInTimeRecoveryDescription", {}).get("PointInTimeRecoveryStatus")
    return any(s == "ENABLED" for s in pitr.values()), {"tables": pitr}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
