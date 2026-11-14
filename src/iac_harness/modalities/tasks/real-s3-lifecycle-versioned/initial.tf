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

resource "aws_s3_bucket" "main" {
  bucket = "e13-s3-lifecycle-931493484341"
}

resource "aws_s3_bucket_versioning" "main" {
  bucket = aws_s3_bucket.main.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_object" "config" {
  bucket  = aws_s3_bucket.main.id
  key     = "app/config.json"
  content = "{}"

  depends_on = [aws_s3_bucket_versioning.main]
}

resource "aws_s3_object" "data" {
  bucket  = aws_s3_bucket.main.id
  key     = "app/data.csv"
  content = "id,name"

  depends_on = [aws_s3_bucket_versioning.main]
}
