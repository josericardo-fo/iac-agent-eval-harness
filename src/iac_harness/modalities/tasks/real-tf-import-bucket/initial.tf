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
  bucket = "e13-import-931493484341"
}

resource "aws_s3_object" "o0" {
  bucket  = aws_s3_bucket.main.id
  key     = "site/index.html"
  content = "<h1>hello</h1>"
}

resource "aws_s3_object" "o1" {
  bucket  = aws_s3_bucket.main.id
  key     = "site/app.js"
  content = "console.log(1)"
}

resource "aws_s3_object" "o2" {
  bucket  = aws_s3_bucket.main.id
  key     = "assets/logo.txt"
  content = "logo"
}
