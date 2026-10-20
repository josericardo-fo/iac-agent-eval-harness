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

resource "aws_iam_user" "auditor" {
  name = "mod-readonly-user"
}

resource "aws_iam_user_policy_attachment" "readonly" {
  user       = aws_iam_user.auditor.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
