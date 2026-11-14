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

provider "aws" {
  alias  = "west"
  region = "us-west-2"
}

data "aws_eip" "orphan" {
  filter {
    name   = "tag:Name"
    values = ["e13-charges-eip"]
  }
}

data "aws_ebs_volume" "orphan" {
  provider    = aws.west
  most_recent = true

  filter {
    name   = "tag:Name"
    values = ["e13-charges-volume"]
  }
}

data "aws_ebs_snapshot" "orphan" {
  provider    = aws.west
  owners      = ["self"]
  most_recent = true

  filter {
    name   = "tag:Name"
    values = ["e13-charges-snapshot"]
  }
}

output "answer" {
  value = {
    elastic_ip = data.aws_eip.orphan.id
    volume     = data.aws_ebs_volume.orphan.id
    snapshot   = data.aws_ebs_snapshot.orphan.id
  }
}
