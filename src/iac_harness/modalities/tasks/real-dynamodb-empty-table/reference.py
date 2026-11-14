import boto3

TABLE = "e13-orders"
ddb = boto3.client("dynamodb")
table = boto3.resource("dynamodb").Table(TABLE)
with table.batch_writer() as batch:
    for page in ddb.get_paginator("scan").paginate(TableName=TABLE, ProjectionExpression="pk"):
        for item in page["Items"]:
            batch.delete_item(Key={"pk": item["pk"]["S"]})
