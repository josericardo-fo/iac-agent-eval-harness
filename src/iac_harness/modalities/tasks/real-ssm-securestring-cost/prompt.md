We looking into storing RDBMS credentials with an alternative method than Secrets Manager, due to cost reasons and and a legacy implementation.

Business wants to keep it lightweight, automated, and resembling Secrets Manager, but they want a custom solution to be coded.

We are not concerned with unauthorized EC2 access from Internet because of the architecture (VPC with no attached IGW), but we need to keep the DB credentials unreadable for the naked eye (legacy reasons for the implementation), in this case support engineers who are working on the servers.

So far we have looked into:
- OpenSSL with key based symmetric ciphers, but that still requires a plaintext password.
- gpg encrypted files.

What are your experiences when asked (management) to stop using Secrets Manager and find an alternative solution in *IX based EC2 instances, with the main requirements being "unreadable to the naked eye" and "do not use AWS Secrets Manager"?
