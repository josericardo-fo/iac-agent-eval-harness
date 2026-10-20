# Modify: enable S3 bucket default encryption with KMS

An S3 bucket named 'mod-encryption-bucket' already exists in us-east-1. Set the default
server-side encryption of this existing bucket to SSE-KMS (algorithm 'aws:kms') using the AWS
managed key for S3 (do not create or specify a customer managed key), with S3 Bucket Key enabled.
Do not create a new bucket.
