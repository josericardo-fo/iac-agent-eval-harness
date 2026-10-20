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

resource "aws_sqs_queue" "orders_dev" {
  name = "del-q-orders-dev"

  tags = {
    env = "dev"
  }
}

resource "aws_sqs_queue" "events_dev" {
  name = "del-q-events-dev"

  tags = {
    env = "dev"
  }
}

resource "aws_sqs_queue" "jobs_dev" {
  name = "del-q-jobs-dev"

  tags = {
    env = "dev"
  }
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
