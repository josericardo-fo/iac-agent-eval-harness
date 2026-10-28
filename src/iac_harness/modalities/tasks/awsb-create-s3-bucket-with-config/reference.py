import json

import boto3

BUCKET = "awsb-ai-testing-a02o0m"
s3 = boto3.client("s3")
s3.create_bucket(Bucket=BUCKET)
s3.put_public_access_block(
    Bucket=BUCKET,
    PublicAccessBlockConfiguration={
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": True,
        "RestrictPublicBuckets": True,
    },
)
s3.put_bucket_encryption(
    Bucket=BUCKET,
    ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
    },
)
s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
tags = {
    "Environment": "Testing",
    "Purpose": "AIOperations",
    "Owner": "AITeam",
    "Project": "AITesting",
}
s3.put_bucket_tagging(
    Bucket=BUCKET, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]}
)
with open("answer.json", "w") as fh:
    json.dump({"S3BucketName": BUCKET}, fh)
