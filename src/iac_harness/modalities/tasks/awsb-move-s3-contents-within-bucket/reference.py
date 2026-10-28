import boto3

BUCKET = "awsb-test-bucket-9779dr"
SOURCE, DESTINATION = "source/", "destination/"

s3 = boto3.client("s3")
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=SOURCE):
    for obj in page.get("Contents", []):
        key = obj["Key"]
        new_key = DESTINATION + key.removeprefix(SOURCE)
        s3.copy_object(Bucket=BUCKET, Key=new_key, CopySource={"Bucket": BUCKET, "Key": key})
        s3.delete_object(Bucket=BUCKET, Key=key)
