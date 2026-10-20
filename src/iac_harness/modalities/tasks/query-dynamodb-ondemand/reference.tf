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

data "aws_dynamodb_tables" "all" {}

data "aws_dynamodb_table" "each" {
  for_each = toset(data.aws_dynamodb_tables.all.names)
  name     = each.value
}

output "answer" {
  value = {
    tables = [for t in data.aws_dynamodb_table.each : t.name if t.billing_mode == "PAY_PER_REQUEST"]
  }
}
