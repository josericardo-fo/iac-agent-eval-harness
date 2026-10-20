import boto3

sqs = boto3.client("sqs")
for url in sqs.list_queues(QueueNamePrefix="del-q-").get("QueueUrls", []):
    if sqs.list_queue_tags(QueueUrl=url).get("Tags", {}).get("env") == "dev":
        sqs.delete_queue(QueueUrl=url)
