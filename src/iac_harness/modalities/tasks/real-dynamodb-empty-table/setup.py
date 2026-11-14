"""Grava ~30 itens na tabela "e13-orders" e a testemunha da tabela no SSM.

A testemunha (/e13/dynamodb-empty-table/witness) guarda ARN, TableId e CreationDateTime: o
check compara com a tabela no fim e distingue esvaziar de apagar e recriar. Roda uma vez,
depois do apply, e nunca vai para o /work.
"""

import json
import sys

import boto3

TABLE = "e13-orders"
WITNESS = "/e13/dynamodb-empty-table/witness"


def main(region: str) -> None:
    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(TABLE)
    with table.batch_writer() as batch:
        for n in range(30):
            batch.put_item(
                Item={"pk": f"order-{n:03d}", "customer_id": f"c-{n % 5}", "total": n * 10}
            )
    desc = boto3.client("dynamodb", region_name=region).describe_table(TableName=TABLE)["Table"]
    witness = {
        "arn": desc["TableArn"],
        "id": desc["TableId"],
        "created": desc["CreationDateTime"].isoformat(),
    }
    boto3.client("ssm", region_name=region).put_parameter(
        Name=WITNESS, Value=json.dumps(witness), Type="String", Overwrite=True
    )


if __name__ == "__main__":
    main(sys.argv[1])
