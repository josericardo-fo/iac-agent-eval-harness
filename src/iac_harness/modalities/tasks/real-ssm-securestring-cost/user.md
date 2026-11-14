The database password is the one stored in Parameter Store as /e13/mariadb/password, and today it is
readable as plain text. It must stop being readable, and keep being available to our
automation. Do not use Secrets Manager, and do not create a new KMS key (cost): the default
AWS-managed key is fine.
