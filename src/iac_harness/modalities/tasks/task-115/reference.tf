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
    region = "us-west-1"
}

resource "aws_s3_bucket" "a" {
  bucket = "pike-680235478471"
}

resource "aws_s3_bucket_public_access_block" "example" {
  bucket = "pike-680235478471"

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
