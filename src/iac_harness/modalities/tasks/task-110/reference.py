import boto3

BUCKET = "wellcomecollection-platform-infra"
s3 = boto3.client("s3")
s3.create_bucket(Bucket=BUCKET)
s3.put_bucket_ownership_controls(
    Bucket=BUCKET, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerPreferred"}]}
)
s3.put_bucket_acl(Bucket=BUCKET, ACL="private")
s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
s3.put_bucket_lifecycle_configuration(
    Bucket=BUCKET,
    LifecycleConfiguration={
        "Rules": [
            {
                "ID": "tmp",
                "Filter": {"Prefix": "tmp/"},
                "Expiration": {"Days": 30},
                "Status": "Enabled",
            },
            {
                "ID": "expire_old_versions",
                "Filter": {},
                "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}],
                "NoncurrentVersionExpiration": {"NoncurrentDays": 90},
                "Status": "Enabled",
            },
        ]
    },
)
