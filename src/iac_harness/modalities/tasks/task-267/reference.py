import boto3

boto3.client("iam").create_virtual_mfa_device(VirtualMFADeviceName="example")
