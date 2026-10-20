# Query: security groups with SSH open to the internet

A VPC tagged Name='query-sg-vpc' exists in us-east-1. Find the security groups in this VPC that
allow inbound SSH (TCP port 22) from anywhere on the internet (0.0.0.0/0). Do not create, modify
or delete any resource.

Answer with this JSON object, listing the security group names (order does not matter):

```json
{"security_groups": ["<security group name>", "..."]}
```
