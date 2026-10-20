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

data "aws_vpc" "main" {
  tags = {
    Name = "query-sg-vpc"
  }
}

data "aws_security_groups" "in_vpc" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }
}

data "aws_vpc_security_group_rules" "in_vpc" {
  filter {
    name   = "group-id"
    values = data.aws_security_groups.in_vpc.ids
  }
}

data "aws_vpc_security_group_rule" "each" {
  for_each               = toset(data.aws_vpc_security_group_rules.in_vpc.ids)
  security_group_rule_id = each.value
}

locals {
  ssh_open_ids = toset([
    for r in data.aws_vpc_security_group_rule.each : r.security_group_id
    if !r.is_egress && r.ip_protocol == "tcp" && r.cidr_ipv4 == "0.0.0.0/0" &&
    r.from_port <= 22 && r.to_port >= 22
  ])
}

data "aws_security_group" "ssh_open" {
  for_each = local.ssh_open_ids
  id       = each.value
}

output "answer" {
  value = {
    security_groups = [for sg in data.aws_security_group.ssh_open : sg.name]
  }
}
