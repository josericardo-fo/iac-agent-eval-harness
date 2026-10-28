import time

import boto3

kv = boto3.client("kinesisvideo")
kv.create_stream(
    StreamName="terraform-kinesis-video-stream",
    DataRetentionInHours=1,
    DeviceName="kinesis-video-device-name",
    MediaType="video/h264",
    Tags={"Name": "terraform-kinesis-video-stream"},
)
while (
    kv.describe_stream(StreamName="terraform-kinesis-video-stream")["StreamInfo"]["Status"]
    != "ACTIVE"
):
    time.sleep(5)
