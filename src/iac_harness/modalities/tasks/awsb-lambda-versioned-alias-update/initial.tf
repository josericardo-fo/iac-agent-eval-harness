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

resource "aws_dynamodb_table" "old" {
  name         = "awsb-old-whg34h5b"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "latest" {
  name         = "awsb-latest-3hg2h54"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  range_key    = "timestamp"

  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "N"
  }
}

resource "aws_iam_role" "validation" {
  name = "iac-harness-awsb-validation"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.validation.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "dynamodb_read" {
  role       = aws_iam_role.validation.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonDynamoDBReadOnlyAccess"
}

# Código inline da stack CDK dynamodb_zx5kclfft (só sem os espaços no fim das linhas vazias).
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
  role             = aws_iam_role.validation.arn
  runtime          = "python3.11"
  handler          = "index.handler"
  timeout          = 30
  filename         = data.archive_file.validation.output_path
  source_code_hash = data.archive_file.validation.output_base64sha256

  environment {
    variables = {
      OLD_TABLE_NAME = aws_dynamodb_table.old.name
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.basic,
    aws_iam_role_policy_attachment.dynamodb_read,
  ]
}
