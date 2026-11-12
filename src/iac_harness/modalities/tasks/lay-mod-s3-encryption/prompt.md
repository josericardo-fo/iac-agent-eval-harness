# Modify: make an S3 bucket encrypt everything with an AWS key

We already have an S3 bucket called 'mod-encryption-bucket' in the N. Virginia region
(us-east-1). Make this existing bucket encrypt every file stored in it by default, using the key
that AWS Key Management Service (KMS) manages for S3 on its own (do not create or pick a key of our
own), and turn on the S3 Bucket Key option, which cuts the cost of that encryption. Do not create
a new bucket.
