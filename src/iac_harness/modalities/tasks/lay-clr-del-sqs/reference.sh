queue_url=$(aws sqs get-queue-url --queue-name e2-orders-dev --query QueueUrl --output text)
aws sqs delete-queue --queue-url "${queue_url}"
