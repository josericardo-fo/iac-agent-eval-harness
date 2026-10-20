aws s3api create-bucket --bucket pike-680235478471
aws s3api put-public-access-block --bucket pike-680235478471 \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
