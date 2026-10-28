terraform {
  required_version = ">= 1.15.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

resource "aws_db_instance" "default" {
  identifier           = "basic-io1-db" # adaptado: nome fixo (ver meta.toml)
  allocated_storage    = 100
  engine               = "mysql"
  instance_class       = "db.t3.micro"
  username             = "foo"
  password             = "foobarbaz"
  storage_type        = "io1"
  iops                 = 1000 # adaptado: io1 exige IOPS (ver meta.toml)
  skip_final_snapshot  = true # adaptado: sem ele o destroy falha (ver meta.toml)
}
