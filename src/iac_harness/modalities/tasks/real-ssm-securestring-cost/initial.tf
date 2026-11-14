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

# Senha do banco guardada em texto puro (tipo String): é o que o usuário quer proteger.
resource "aws_ssm_parameter" "db_password" {
  name  = "/e13/mariadb/password"
  type  = "String"
  value = "Tr0ub4dor-e13-Pa55"
}
