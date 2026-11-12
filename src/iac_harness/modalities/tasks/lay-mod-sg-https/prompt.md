# Modify: let secure web traffic into an existing firewall

In the N. Virginia region (us-east-1) we already have a private network (VPC) named 'mod-sg-vpc'.
Inside it there is a security group, the firewall for our servers, called 'mod-web-sg'. Today it
lets in normal web traffic (HTTP, port 80) from anyone on the internet. Also let in secure web
traffic (HTTPS, port 443) from anyone on the internet, and keep what it already allows. Do not
create a new security group.
