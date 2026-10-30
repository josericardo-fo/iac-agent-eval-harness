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

resource "aws_s3_bucket" "logs_old" {
  bucket = "del-logs-old"
}

resource "aws_s3_object" "old_log" {
  bucket  = aws_s3_bucket.logs_old.id
  key     = "app/2024-01.log"
  content = "old entries"
}

resource "aws_s3_bucket" "logs_current" {
  bucket = "del-logs-current"
}

resource "aws_s3_object" "current_log" {
  bucket  = aws_s3_bucket.logs_current.id
  key     = "app/2025-01.log"
  content = "current entries"
}
