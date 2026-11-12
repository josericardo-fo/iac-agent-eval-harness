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

resource "aws_s3_bucket_versioning" "logs_old" {
  bucket = aws_s3_bucket.logs_old.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_object" "app_log" {
  bucket     = aws_s3_bucket.logs_old.id
  key        = "app/2024-01.log"
  content    = "first version"
  depends_on = [aws_s3_bucket_versioning.logs_old]
}

# Mesma chave gravada de novo: gera uma segunda versão do objeto.
resource "aws_s3_object" "app_log_v2" {
  bucket     = aws_s3_bucket.logs_old.id
  key        = "app/2024-01.log"
  content    = "second version"
  depends_on = [aws_s3_object.app_log]
}

resource "aws_s3_object" "audit_log" {
  bucket     = aws_s3_bucket.logs_old.id
  key        = "audit/2024-01.log"
  content    = "audit entries"
  depends_on = [aws_s3_bucket_versioning.logs_old]
}

resource "aws_s3_bucket" "keep" {
  bucket = "del-keep-bucket"
}
