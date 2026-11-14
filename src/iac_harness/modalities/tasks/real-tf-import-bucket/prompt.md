I have a beginner question about importing existing AWS resources such as: S3 into Terraform.

For example, below I have the provider config information as well as the resource.

// Provider configuration
terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "bucket" {
  bucket = "e13-import-931493484341"
}

The question(s) I have;

When I run the specific import commands to import my existing S3 bucket from AWS, I assume those resources will show up on my .tfstate file? Do I still need to add more to the resource config file on my main.tf?

Or, once I have imported it, I can add changes on top of what has already been set?
Kind of confused about the next steps for making additional changes to my existing resources through terraform. My goal is to convert my existing S3 bucket configuration that has been already created on the AWS console onto terraform.
