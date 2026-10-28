terraform {
  required_version = ">= 1.15.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

resource "aws_lightsail_database" "test" {
  relational_database_name = "test"
  availability_zone        = "us-east-1a"
  master_database_name     = "testdatabasename"
  master_password          = "testdatabasepassword"
  master_username          = "test"
  blueprint_id             = "mysql_8_4"
  bundle_id                = "micro_2_0"
}
