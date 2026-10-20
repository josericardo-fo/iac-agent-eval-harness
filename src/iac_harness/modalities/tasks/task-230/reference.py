import boto3

ddb = boto3.client("dynamodb")
ddb.create_table(
    TableName="your-dynamodb-table-name",
    AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
    KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
    BillingMode="PAY_PER_REQUEST",
)
ddb.get_waiter("table_exists").wait(TableName="your-dynamodb-table-name")
ddb.update_continuous_backups(
    TableName="your-dynamodb-table-name",
    PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
)
