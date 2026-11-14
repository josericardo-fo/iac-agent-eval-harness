aws s3api create-bucket --bucket e13-public-931493484341
aws s3api put-public-access-block --bucket e13-public-931493484341 \
    --public-access-block-configuration \
    BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false
aws s3api put-bucket-policy --bucket e13-public-931493484341 --policy '{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::e13-public-931493484341/*"
  }]
}'
