import boto3

BUCKET = "e13-s3-delete-versions-931493484341"
s3 = boto3.client("s3")
for prefix in ("logs/", "tmp/"):
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=BUCKET, Prefix=prefix):
        found = page.get("Versions", []) + page.get("DeleteMarkers", [])
        if found:
            s3.delete_objects(
                Bucket=BUCKET,
                Delete={"Objects": [{"Key": o["Key"], "VersionId": o["VersionId"]} for o in found]},
            )
