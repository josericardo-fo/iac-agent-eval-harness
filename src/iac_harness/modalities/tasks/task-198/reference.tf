terraform {
  required_version = ">= 1.15.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# adaptado: ids em minúsculas e 3 usuários, como pede o enunciado (ver meta.toml)
resource "aws_elasticache_user" "test" {
  user_id       = "testuserid"
  user_name     = "default"
  access_string = "on ~app::* -@all +@read +@hash +@bitmap +@geo -setbit -bitfield -hset -hsetnx -hmset -hincrby -hincrbyfloat -hdel -bitop -geoadd -georadius -georadiusbymember"
  engine        = "REDIS"
  passwords     = ["password123456789"]
}

resource "aws_elasticache_user" "user2" {
  user_id       = "app-user-2"
  user_name     = "appuser2"
  access_string = "on ~app::* -@all +@read +@hash +@bitmap +@geo -setbit -bitfield -hset -hsetnx -hmset -hincrby -hincrbyfloat -hdel -bitop -geoadd -georadius -georadiusbymember"
  engine        = "REDIS"
  passwords     = ["password123456789"]
}

resource "aws_elasticache_user" "user3" {
  user_id       = "app-user-3"
  user_name     = "appuser3"
  access_string = "on ~app::* -@all +@read +@hash +@bitmap +@geo -setbit -bitfield -hset -hsetnx -hmset -hincrby -hincrbyfloat -hdel -bitop -geoadd -georadius -georadiusbymember"
  engine        = "REDIS"
  passwords     = ["password123456789"]
}

resource "aws_elasticache_user_group" "test" {
  engine        = "REDIS"
  user_group_id = "usergroupid"
  user_ids = [
    aws_elasticache_user.test.user_id,
    aws_elasticache_user.user2.user_id,
    aws_elasticache_user.user3.user_id,
  ]
}
