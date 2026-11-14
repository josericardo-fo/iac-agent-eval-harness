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

# Elastic IP solto (sem instância nem interface associada).
resource "aws_eip" "orphan" {
  domain = "vpc"

  tags = {
    Name = "e13-charges-eip"
  }
}

# Volume gp3 de 1 GiB sem instância, e o snapshot dele, na região "esquecida".
resource "aws_ebs_volume" "orphan" {
  provider          = aws.west
  availability_zone = "us-west-2a"
  size              = 1
  type              = "gp3"

  tags = {
    Name = "e13-charges-volume"
  }
}

resource "aws_ebs_snapshot" "orphan" {
  provider  = aws.west
  volume_id = aws_ebs_volume.orphan.id

  tags = {
    Name = "e13-charges-snapshot"
  }
}
