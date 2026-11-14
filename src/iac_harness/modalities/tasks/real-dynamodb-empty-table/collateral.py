"""Retrato do efeito colateral: o que deve ficar igual depois de esvaziar a tabela "e13-orders".

Guarda a lista de tabelas e, em cada uma, ARN, TableId e data de criação (recriada = valores
novos), chave, atributos, modo de cobrança, índices globais (nome, chave, projeção) e tags. Os
itens (alvo) ficam de fora, e a testemunha do SSM também.
"""

import json
import sys

import boto3


def main(region: str) -> dict:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = sorted(ddb.list_tables().get("TableNames", []))
    snap: dict = {"tables": tables}
    for name in tables:
        t = ddb.describe_table(TableName=name)["Table"]
        tags = ddb.list_tags_of_resource(ResourceArn=t["TableArn"]).get("Tags", [])
        snap[name] = {
            "arn": t.get("TableArn"),
            "id": t.get("TableId"),
            "created": t.get("CreationDateTime"),
            "key_schema": t.get("KeySchema"),
            "attributes": sorted(
                t.get("AttributeDefinitions", []), key=lambda a: a["AttributeName"]
            ),
            "billing": t.get("BillingModeSummary", {}).get("BillingMode"),
            "gsis": sorted(
                [g["IndexName"], g.get("KeySchema"), g.get("Projection")]
                for g in t.get("GlobalSecondaryIndexes", [])
            ),
            "tags": sorted((x["Key"], x["Value"]) for x in tags),
        }
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
