import boto3

boto3.client("s3").put_bucket_versioning(
    Bucket="mod-versioning-bucket", VersioningConfiguration={"Status": "Enabled"}
)
