import json

import boto3

policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "s3.amazonaws.com"},
            "Action": "SNS:Publish",
            "Resource": "arn:aws:sns:*:*:s3-event-notification-topic",
            "Condition": {"ArnLike": {"aws:SourceArn": "arn:aws:s3:::your-bucket-name"}},
        }
    ],
}
sns = boto3.client("sns")
topic_arn = sns.create_topic(
    Name="s3-event-notification-topic", Attributes={"Policy": json.dumps(policy)}
)["TopicArn"]
s3 = boto3.client("s3")
s3.create_bucket(Bucket="your-bucket-name")
s3.put_bucket_notification_configuration(
    Bucket="your-bucket-name",
    NotificationConfiguration={
        "TopicConfigurations": [
            {
                "TopicArn": topic_arn,
                "Events": ["s3:ObjectCreated:*"],
                "Filter": {"Key": {"FilterRules": [{"Name": "suffix", "Value": ".log"}]}},
            }
        ]
    },
)
