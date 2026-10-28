terraform {
  required_version = ">= 1.15.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "data" {
  bucket        = "awsb-test-bucket-9779dr"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

locals {
  objects = {
    "source/file1.txt"            = { content = "Sample content 1", type = "text/plain" }
    "source/file2.json"           = { content = "{\"key\": \"value\"}", type = "application/json" }
    "source/subfolder/file3.txt"  = { content = "Nested content", type = "text/plain" }
    "destination/placeholder.txt" = { content = "Ready", type = "text/plain" }
  }
}

resource "aws_s3_object" "seed" {
  for_each     = local.objects
  bucket       = aws_s3_bucket.data.id
  key          = each.key
  content      = each.value.content
  content_type = each.value.type

  depends_on = [aws_s3_bucket_versioning.data]
}
