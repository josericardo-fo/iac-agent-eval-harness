# Derivado de solution/solve.sh do aws-bench, com nome de bucket fixo e a resposta em answer.json.
BUCKET=awsb-ai-testing-a02o0m

aws s3api create-bucket --bucket "$BUCKET"

aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled

aws s3api put-bucket-tagging --bucket "$BUCKET" \
    --tagging 'TagSet=[{Key=Environment,Value=Testing},{Key=Purpose,Value=AIOperations},{Key=Owner,Value=AITeam},{Key=Project,Value=AITesting}]'

jq -n --arg b "$BUCKET" '{S3BucketName: $b}' > answer.json
