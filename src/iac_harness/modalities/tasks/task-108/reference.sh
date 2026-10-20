aws s3api create-bucket --bucket sample
aws s3api put-bucket-versioning --bucket sample \
    --versioning-configuration Status=Enabled --expected-bucket-owner 123456789012
