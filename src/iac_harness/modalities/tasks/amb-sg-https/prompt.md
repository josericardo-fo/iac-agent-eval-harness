# Modify: allow inbound HTTPS on an existing security group

A VPC tagged Name='e2-sg-vpc' already exists in us-east-1. Add an inbound rule to its security
group allowing TCP port 443 from 0.0.0.0/0, keeping the existing rules. Do not create a new
security group.
