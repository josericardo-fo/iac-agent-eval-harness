import boto3

boto3.client("dynamodb").update_continuous_backups(
    TableName="e2-pitr-orders",
    PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
)
