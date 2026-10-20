aws dynamodb create-table --table-name your-dynamodb-table-name \
    --attribute-definitions AttributeName=id,AttributeType=S \
    --key-schema AttributeName=id,KeyType=HASH --billing-mode PAY_PER_REQUEST
aws dynamodb wait table-exists --table-name your-dynamodb-table-name
aws dynamodb update-continuous-backups --table-name your-dynamodb-table-name \
    --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true
