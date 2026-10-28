import boto3

TABLE = "awsb-table-1getshy"

ddb = boto3.client("dynamodb")
pages = ddb.get_paginator("scan").paginate(
    TableName=TABLE,
    FilterExpression="attribute_exists(ec2_instance_id) OR attribute_exists(ec2_instance_type)",
    ProjectionExpression="id",
)
for page in pages:
    for item in page["Items"]:
        ddb.update_item(
            TableName=TABLE,
            Key={"id": item["id"]},
            UpdateExpression="REMOVE ec2_instance_id, ec2_instance_type",
        )
