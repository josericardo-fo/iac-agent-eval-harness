terraform {
  required_version = ">= 1.15.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source = "hashicorp/archive"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# A função já existe e não está neste estado: o import a traz para o Terraform, e o apply só
# troca o valor da variável de ambiente (mesmo código, mesma role).
import {
  to = aws_lambda_function.validation
  id = "awsb-validation-function"
}

data "aws_iam_role" "validation" {
  name = "iac-harness-awsb-validation"
}

data "archive_file" "validation" {
  type        = "zip"
  output_path = "${path.module}/validation.zip"

  source {
    filename = "index.py"
    content  = <<-PY
      import boto3
      import json
      import os

      def handler(event, context):
          dynamodb = boto3.resource('dynamodb')

          table = dynamodb.Table(os.environ['OLD_TABLE_NAME'])

          try:
              response = table.get_item(Key={'id': event.get('validation_id', 'default')})

              if 'Item' not in response:
                  return {'statusCode': 404, 'body': json.dumps({'error': 'Validation data not found'})}

              return {
                  'statusCode': 200,
                  'body': json.dumps({'validation_id': response['Item'].get('id'), 'basic_data': response['Item'].get('data')})
              }
          except Exception as e:
              return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
    PY
  }
}

resource "aws_lambda_function" "validation" {
  function_name    = "awsb-validation-function"
  role             = data.aws_iam_role.validation.arn
  runtime          = "python3.11"
  handler          = "index.handler"
  timeout          = 30
  filename         = data.archive_file.validation.output_path
  source_code_hash = data.archive_file.validation.output_base64sha256

  environment {
    variables = {
      OLD_TABLE_NAME = "awsb-latest-3hg2h54"
    }
  }
}
