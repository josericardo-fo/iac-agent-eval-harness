aws kinesisvideo create-stream --stream-name terraform-kinesis-video-stream \
    --data-retention-in-hours 1 --device-name kinesis-video-device-name \
    --media-type video/h264 --tags Name=terraform-kinesis-video-stream > /dev/null
until [ "$(aws kinesisvideo describe-stream --stream-name terraform-kinesis-video-stream \
    --query StreamInfo.Status --output text)" = ACTIVE ]; do
    sleep 5
done
