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

resource "aws_sqs_queue" "orders_prod" {
  name = "del-q-orders-prod"

  tags = {
    env = "prod"
  }
}

resource "aws_sqs_queue" "events_prod" {
  name = "del-q-events-prod"

  tags = {
    env = "prod"
  }
}
