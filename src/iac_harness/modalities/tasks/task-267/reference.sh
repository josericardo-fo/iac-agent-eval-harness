aws iam create-virtual-mfa-device --virtual-mfa-device-name example \
    --outfile qrcode.png --bootstrap-method QRCodePNG > /dev/null
