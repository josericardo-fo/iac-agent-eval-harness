"""Estado intocado: as duas tabelas existem e nenhuma tem point-in-time recovery."""

import json
import sys

import boto3

TABLES = ["e2-pitr-orders", "e2-pitr-sessions"]


def _pitr(ddb, table: str) -> str | None:
    desc = ddb.describe_continuous_backups(TableName=table)["ContinuousBackupsDescription"]
    return desc.get("PointInTimeRecoveryDescription", {}).get("PointInTimeRecoveryStatus")


def main(region: str) -> tuple[bool, dict]:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = ddb.list_tables().get("TableNames", [])
    missing = [t for t in TABLES if t not in tables]
    if missing:
        return False, {"missing": missing, "tables": tables}
    pitr = {t: _pitr(ddb, t) for t in TABLES}
    return all(s != "ENABLED" for s in pitr.values()), {"pitr": pitr}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
