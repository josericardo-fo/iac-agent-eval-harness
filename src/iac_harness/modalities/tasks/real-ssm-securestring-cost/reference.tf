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

# A mesma senha, agora SecureString (chave gerenciada alias/aws/ssm, sem custo).
resource "aws_ssm_parameter" "db_password" {
  name  = "/e13/mariadb/password"
  type  = "SecureString"
  value = "Tr0ub4dor-e13-Pa55"
}
