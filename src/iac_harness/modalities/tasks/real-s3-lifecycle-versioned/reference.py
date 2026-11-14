import boto3

boto3.client("s3").put_bucket_lifecycle_configuration(
    Bucket="e13-s3-lifecycle-931493484341",
    LifecycleConfiguration={
        "Rules": [
            {
                "ID": "keep-10-noncurrent",
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "NoncurrentVersionExpiration": {
                    "NoncurrentDays": 30,
                    "NewerNoncurrentVersions": 10,
                },
                "Expiration": {"ExpiredObjectDeleteMarker": True},
            }
        ]
    },
)
