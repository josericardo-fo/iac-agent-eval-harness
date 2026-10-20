bucket=wellcomecollection-platform-infra
aws s3api create-bucket --bucket "$bucket"
aws s3api put-bucket-ownership-controls --bucket "$bucket" \
    --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerPreferred}]'
aws s3api put-bucket-acl --bucket "$bucket" --acl private
aws s3api put-bucket-versioning --bucket "$bucket" --versioning-configuration Status=Enabled
aws s3api put-bucket-lifecycle-configuration --bucket "$bucket" \
    --lifecycle-configuration '{
      "Rules": [
        {"ID": "tmp", "Filter": {"Prefix": "tmp/"}, "Expiration": {"Days": 30},
         "Status": "Enabled"},
        {"ID": "expire_old_versions", "Filter": {},
         "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}],
         "NoncurrentVersionExpiration": {"NoncurrentDays": 90}, "Status": "Enabled"}
      ]
    }'
