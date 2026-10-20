import boto3

ddb = boto3.client("dynamodb")
ddb.create_table(
    TableName="your-dynamodb-table-name",
    AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
    KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
    BillingMode="PAY_PER_REQUEST",
    StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"},
)
ddb.get_waiter("table_exists").wait(TableName="your-dynamodb-table-name")
