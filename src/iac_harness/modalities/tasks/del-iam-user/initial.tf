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

resource "aws_iam_user" "legacy" {
  name = "del-svc-legacy"
}

resource "aws_iam_access_key" "legacy" {
  user = aws_iam_user.legacy.name
}

resource "aws_iam_user_policy" "legacy" {
  name = "del-svc-legacy-s3"
  user = aws_iam_user.legacy.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_user_group_membership" "legacy" {
  user   = aws_iam_user.legacy.name
  groups = [aws_iam_group.ops.name]
}
