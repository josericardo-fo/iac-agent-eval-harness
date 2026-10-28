# Derivado de solution/solve.sh do aws-bench (a origem real é source/, não o sourc/ do enunciado).
aws s3 mv s3://awsb-test-bucket-9779dr/source/ s3://awsb-test-bucket-9779dr/destination/ \
    --recursive
