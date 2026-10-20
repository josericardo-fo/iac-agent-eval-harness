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

resource "aws_iam_group" "ops" {
  name = "del-ops"
}

resource "aws_iam_group_policy_attachment" "ops_readonly" {
  group      = aws_iam_group.ops.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
