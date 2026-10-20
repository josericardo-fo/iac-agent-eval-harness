import boto3

boto3.client("s3").put_bucket_encryption(
    Bucket="mod-encryption-bucket",
    ServerSideEncryptionConfiguration={
        "Rules": [
            {
                "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"},
                "BucketKeyEnabled": True,
            }
        ]
    },
)
