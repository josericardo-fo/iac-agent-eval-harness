import boto3

BUCKET = "del-logs-old"

s3 = boto3.client("s3")
# Esvazia o bucket antes de apagá-lo.
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
    objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
    if objects:
        s3.delete_objects(Bucket=BUCKET, Delete={"Objects": objects})
s3.delete_bucket(Bucket=BUCKET)
