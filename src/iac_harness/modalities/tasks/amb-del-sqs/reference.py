import boto3

sqs = boto3.client("sqs")
sqs.delete_queue(QueueUrl=sqs.get_queue_url(QueueName="e2-orders-dev")["QueueUrl"])
