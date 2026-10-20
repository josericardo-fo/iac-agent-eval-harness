import json

import boto3

dynamodb = boto3.client("dynamodb")
names = []
for page in dynamodb.get_paginator("list_tables").paginate():
    names.extend(page["TableNames"])

on_demand = []
for name in names:
    table = dynamodb.describe_table(TableName=name)["Table"]
    if table.get("BillingModeSummary", {}).get("BillingMode") == "PAY_PER_REQUEST":
        on_demand.append(name)

with open("answer.json", "w") as fh:
    json.dump({"tables": on_demand}, fh)
