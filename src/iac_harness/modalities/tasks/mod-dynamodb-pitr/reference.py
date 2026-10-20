import boto3

boto3.client("dynamodb").update_continuous_backups(
    TableName="mod-pitr-table",
    PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
)
