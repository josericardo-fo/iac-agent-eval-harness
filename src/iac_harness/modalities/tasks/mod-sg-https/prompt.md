# Modify: allow inbound HTTPS on an existing security group

A VPC tagged Name='mod-sg-vpc' already exists in us-east-1, with a security group named
'mod-web-sg' in it that allows inbound HTTP (TCP 80) from 0.0.0.0/0. Add an inbound rule to this
existing security group allowing TCP port 443 from 0.0.0.0/0, keeping the existing rules. Do not
create a new security group.
