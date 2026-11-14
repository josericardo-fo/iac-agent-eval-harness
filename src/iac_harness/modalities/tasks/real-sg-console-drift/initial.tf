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

resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"

  tags = {
    Name = "e13-drift-vpc"
  }
}

resource "aws_security_group" "app" {
  name        = "e13-drift-sg"
  description = "Application security group"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "e13-drift-sg"
  }
}

resource "aws_security_group_rule" "https" {
  type              = "ingress"
  security_group_id = aws_security_group.app.id
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
}
