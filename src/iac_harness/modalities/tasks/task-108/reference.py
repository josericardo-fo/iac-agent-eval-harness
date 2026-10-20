import boto3

s3 = boto3.client("s3")
s3.create_bucket(Bucket="sample")
s3.put_bucket_versioning(
    Bucket="sample",
    VersioningConfiguration={"Status": "Enabled"},
    ExpectedBucketOwner="123456789012",
)
