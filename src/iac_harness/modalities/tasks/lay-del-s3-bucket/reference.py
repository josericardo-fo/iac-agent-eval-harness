import boto3

BUCKET = "del-logs-old"

s3 = boto3.client("s3")
# Esvazia o bucket (todas as versões e delete markers) antes de apagá-lo.
for page in s3.get_paginator("list_object_versions").paginate(Bucket=BUCKET):
    objects = [
        {"Key": v["Key"], "VersionId": v["VersionId"]}
        for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
    ]
    if objects:
        s3.delete_objects(Bucket=BUCKET, Delete={"Objects": objects})
s3.delete_bucket(Bucket=BUCKET)
