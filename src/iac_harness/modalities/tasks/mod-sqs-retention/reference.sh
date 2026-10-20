queue_url=$(aws sqs get-queue-url --queue-name mod-retention-queue \
    --query QueueUrl --output text)
aws sqs set-queue-attributes --queue-url "${queue_url}" \
    --attributes MessageRetentionPeriod=345600
