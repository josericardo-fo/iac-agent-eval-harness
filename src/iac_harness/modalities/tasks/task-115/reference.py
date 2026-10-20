import boto3

s3 = boto3.client("s3")
s3.create_bucket(Bucket="pike-680235478471")
s3.put_public_access_block(
    Bucket="pike-680235478471",
    PublicAccessBlockConfiguration={
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": True,
        "RestrictPublicBuckets": True,
    },
)
