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

resource "aws_kinesis_video_stream" "default" {
  name                    = "terraform-kinesis-video-stream"
  data_retention_in_hours = 1
  device_name             = "kinesis-video-device-name"
  media_type              = "video/h264"

  tags = {
    Name = "terraform-kinesis-video-stream"
  }
}
