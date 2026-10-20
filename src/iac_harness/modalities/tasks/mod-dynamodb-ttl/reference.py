import boto3

boto3.client("dynamodb").update_time_to_live(
    TableName="mod-ttl-table",
    TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
)
