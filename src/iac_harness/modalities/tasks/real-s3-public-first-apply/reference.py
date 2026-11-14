import json

import boto3

BUCKET = "e13-public-931493484341"
s3 = boto3.client("s3")
s3.create_bucket(Bucket=BUCKET)
s3.put_public_access_block(
    Bucket=BUCKET,
    PublicAccessBlockConfiguration={
        "BlockPublicAcls": False,
        "IgnorePublicAcls": False,
        "BlockPublicPolicy": False,
        "RestrictPublicBuckets": False,
    },
)
s3.put_bucket_policy(
    Bucket=BUCKET,
    Policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{BUCKET}/*",
                }
            ],
        }
    ),
)
