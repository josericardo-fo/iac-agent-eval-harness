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

import {
  to = aws_s3_bucket.bucket
  id = "e13-import-931493484341"
}

resource "aws_s3_bucket" "bucket" {
  bucket = "e13-import-931493484341"
}
