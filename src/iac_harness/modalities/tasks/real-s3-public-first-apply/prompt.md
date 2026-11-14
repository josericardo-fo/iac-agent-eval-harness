Creating a public bucket doesn't work on the first try

Expected Behavior

It should attach a bucket policy on the first try.

Actual Behavior

It takes 2 times to make it apply. It seems to succeed sometimes on the first try though.

Relevant Error/Panic Output Snippet

```shell
aws_s3_bucket.b: Creating...
aws_s3_bucket.b: Creation complete after 1s [id=e13-public-931493484341]
aws_s3_bucket_policy.b: Creating...
aws_s3_bucket_public_access_block.b: Creating...
aws_s3_bucket_public_access_block.b: Creation complete after 1s [id=e13-public-931493484341]
╷
│ Error: Error putting S3 policy: AccessDenied: Access Denied
│       status code: 403, request id: ..., host id: ...
│ 
│   with aws_s3_bucket_policy.b,
│   on main.tf line 9, in resource "aws_s3_bucket_policy" "b":
│    9: resource "aws_s3_bucket_policy" "b" {
│ 
╵
```

Terraform Configuration Files

```tf
provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "b" {
  bucket = "e13-public-931493484341"
}

resource "aws_s3_bucket_policy" "b" {
  bucket = aws_s3_bucket.b.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = "*"
        Action = "s3:GetObject"
        Resource = "${aws_s3_bucket.b.arn}/*"
      }
    ]
  })
}

resource "aws_s3_bucket_public_access_block" "b" {
  bucket = aws_s3_bucket.b.id
}
```

Steps to Reproduce

```
$ terraform init && terraform apply
```
