terraform {
  required_version = ">= 1.15.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

resource "aws_dax_parameter_group" "example" {
name = "example"

parameters {
name = "query-ttl-millis"
value = "100000"
}

parameters {
name = "record-ttl-millis"
value = "100000"
}
}
