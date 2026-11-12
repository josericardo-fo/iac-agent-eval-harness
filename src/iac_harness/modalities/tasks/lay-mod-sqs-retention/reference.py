import boto3

sqs = boto3.client("sqs")
queue_url = sqs.get_queue_url(QueueName="mod-retention-queue")["QueueUrl"]
sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={"MessageRetentionPeriod": "345600"})
