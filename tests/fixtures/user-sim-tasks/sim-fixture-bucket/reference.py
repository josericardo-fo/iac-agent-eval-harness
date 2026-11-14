import boto3

s3 = boto3.client("s3", region_name="us-east-1")
s3.put_bucket_versioning(Bucket="sim-fixture-logs-b", VersioningConfiguration={"Status": "Enabled"})
