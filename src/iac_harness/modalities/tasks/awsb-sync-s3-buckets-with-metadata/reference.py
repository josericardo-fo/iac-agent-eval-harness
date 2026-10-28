import boto3

SOURCE = "awsb-sync-source-nv0qp1"
DESTINATION = "awsb-sync-destination-nv0qp1"

s3 = boto3.client("s3")
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=SOURCE):
    for obj in page.get("Contents", []):
        s3.copy_object(
            Bucket=DESTINATION,
            Key=obj["Key"],
            CopySource={"Bucket": SOURCE, "Key": obj["Key"]},
            MetadataDirective="REPLACE",
            Metadata={"migrated": "true"},
            ACL="bucket-owner-full-control",
        )
