"""A tabela "e13-orders" é a mesma (ARN, TableId e CreationDateTime iguais aos da testemunha gravada
pelo setup.py), segue com o GSI "by-customer" e as tags Project/Env, e não tem nenhum item."""

import json
import sys

import boto3

TABLE = "e13-orders"
WITNESS = "/e13/dynamodb-empty-table/witness"
TAGS = {"Project": "e13", "Env": "test"}
GSI = "by-customer"


def _count(ddb) -> int:
    """Itens por Scan (o ItemCount do describe_table só atualiza de tempos em tempos)."""
    total = 0
    for page in ddb.get_paginator("scan").paginate(TableName=TABLE, Select="COUNT"):
        total += page["Count"]
    return total


def main(region: str) -> tuple[bool, dict]:
    ddb = boto3.client("dynamodb", region_name=region)
    if TABLE not in ddb.list_tables().get("TableNames", []):
        return False, {"table_exists": False}
    desc = ddb.describe_table(TableName=TABLE)["Table"]
    witness = json.loads(
        boto3.client("ssm", region_name=region).get_parameter(Name=WITNESS)["Parameter"]["Value"]
    )
    same = (
        desc["TableArn"] == witness["arn"]
        and desc["TableId"] == witness["id"]
        and desc["CreationDateTime"].isoformat() == witness["created"]
    )
    tags = {
        t["Key"]: t["Value"]
        for t in ddb.list_tags_of_resource(ResourceArn=desc["TableArn"]).get("Tags", [])
    }
    gsis = [g["IndexName"] for g in desc.get("GlobalSecondaryIndexes", [])]
    items = _count(ddb)
    details = {
        "same_table": same, "gsis": gsis, "tags": tags, "items": items,
    }  # fmt: skip
    ok = same and GSI in gsis and TAGS.items() <= tags.items() and items == 0
    return ok, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
