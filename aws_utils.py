import boto3
import json
import time
import uuid

import os

IAM_ROLE_NAME = "vmimport"
IAM_POLICY_NAME = "vmimport"
TARGET_ACCOUNT_ID = "662863940582"

def get_clients():
    s3_client = boto3.client('s3')
    iam_client = boto3.client('iam')
    ec2_client = boto3.client('ec2')
    return s3_client, iam_client, ec2_client

def create_temp_bucket(s3_client):
    bucket_name = f"ovastack-import-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    print(f"[*] Creating temporary S3 bucket: {bucket_name}")
    try:
        current_region = s3_client.meta.region_name
        if current_region == 'us-east-1':
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': current_region}
            )
            
        # Configure CORS for browser uploads
        cors_configuration = {
            'CORSRules': [{
                'AllowedHeaders': ['*'],
                'AllowedMethods': ['POST', 'PUT'],
                'AllowedOrigins': ['*'], # Can be locked down in production
                'ExposeHeaders': ['ETag']
            }]
        }
        s3_client.put_bucket_cors(Bucket=bucket_name, CORSConfiguration=cors_configuration)
        
        # Enforce lifecycle rule to abort incomplete multipart uploads
        lifecycle_config = {
            'Rules': [
                {
                    'ID': 'AbortIncompleteMultipartUploads',
                    'Status': 'Enabled',
                    'Filter': {'Prefix': ''},
                    'AbortIncompleteMultipartUpload': {
                        'DaysAfterInitiation': 2
                    }
                }
            ]
        }
        s3_client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_config
        )
        
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        pass
        
    return bucket_name

def generate_presigned_post(s3_client, bucket_name, object_name):
    # Generate a presigned S3 POST URL
    response = s3_client.generate_presigned_post(
        bucket_name,
        object_name,
        Fields=None,
        Conditions=[
            ["starts-with", "$key", ""]
        ],
        ExpiresIn=43200
    )
    return response

def create_multipart_upload(s3_client, bucket_name, object_name):
    response = s3_client.create_multipart_upload(Bucket=bucket_name, Key=object_name)
    return response['UploadId']

def generate_presigned_part_url(s3_client, bucket_name, object_name, upload_id, part_number):
    url = s3_client.generate_presigned_url(
        ClientMethod='upload_part',
        Params={
            'Bucket': bucket_name,
            'Key': object_name,
            'UploadId': upload_id,
            'PartNumber': part_number
        },
        ExpiresIn=43200
    )
    return url

def complete_multipart_upload(s3_client, bucket_name, object_name, upload_id, parts):
    s3_client.complete_multipart_upload(
        Bucket=bucket_name,
        Key=object_name,
        UploadId=upload_id,
        MultipartUpload={'Parts': parts}
    )

def create_iam_role_and_policy(iam_client, s3_bucket):
    print("[*] Configuring IAM roles and permissions...")
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "vmie.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"sts:ExternalId": "vmimport"}}
        }]
    }
    try:
        iam_client.get_role(RoleName=IAM_ROLE_NAME)
    except iam_client.exceptions.NoSuchEntityException:
        iam_client.create_role(RoleName=IAM_ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust_policy))

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation", "s3:GetObject", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{s3_bucket}", f"arn:aws:s3:::{s3_bucket}/*"]
            },
            {
                "Effect": "Allow",
                "Action": ["ec2:ModifySnapshotAttribute", "ec2:CopySnapshot", "ec2:RegisterImage", "ec2:Describe*"],
                "Resource": "*"
            }
        ]
    }
    iam_client.put_role_policy(RoleName=IAM_ROLE_NAME, PolicyName=IAM_POLICY_NAME, PolicyDocument=json.dumps(role_policy))
    # Give AWS time to propagate the IAM role
    time.sleep(5) 

def start_import_task(ec2_client, bucket_name, s3_key):
    print("[*] Initiating AMI Import...")
    disk_container = {
        "Description": "Hack Smarter OVA",
        "Format": "ova",
        "UserBucket": {"S3Bucket": bucket_name, "S3Key": s3_key}
    }
    response = ec2_client.import_image(Description="Hack Smarter Image", DiskContainers=[disk_container], Encrypted=False)
    return response['ImportTaskId']

def check_import_status(ec2_client, task_id):
    response = ec2_client.describe_import_image_tasks(ImportTaskIds=[task_id])
    task = response['ImportImageTasks'][0]
    return task

def share_resources(ec2_client, ami_id, target_account=TARGET_ACCOUNT_ID):
    print(f"[*] Sharing AMI and Storage with Account {target_account}...")
    ec2_client.modify_image_attribute(
        ImageId=ami_id,
        LaunchPermission={'Add': [{'UserId': target_account}]}
    )
    
    image_info = ec2_client.describe_images(ImageIds=[ami_id])['Images'][0]
    for device in image_info.get('BlockDeviceMappings', []):
        if 'Ebs' in device:
            ec2_client.modify_snapshot_attribute(
                SnapshotId=device['Ebs']['SnapshotId'],
                Attribute='createVolumePermission',
                OperationType='add',
                UserIds=[target_account]
            )

def cleanup_bucket(s3_client, bucket_name, s3_key):
    print(f"[*] Cleaning up temporary S3 bucket...")
    try:
        s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
        s3_client.delete_bucket(Bucket=bucket_name)
    except Exception as e:
        print(f"Error cleaning up bucket: {e}")

def delete_upload_resources(s3_client, ec2_client, bucket_name, ami_id):
    if ami_id:
        try:
            image_info = ec2_client.describe_images(ImageIds=[ami_id]).get('Images', [])
            if image_info:
                print(f"[*] Deregistering AMI {ami_id}...")
                ec2_client.deregister_image(ImageId=ami_id)
                for device in image_info[0].get('BlockDeviceMappings', []):
                    if 'Ebs' in device and 'SnapshotId' in device['Ebs']:
                        snapshot_id = device['Ebs']['SnapshotId']
                        print(f"[*] Deleting Snapshot {snapshot_id}...")
                        try:
                            ec2_client.delete_snapshot(SnapshotId=snapshot_id)
                        except Exception as e:
                            print(f"Error deleting snapshot {snapshot_id}: {e}")
        except Exception as e:
            print(f"Error deregistering AMI {ami_id}: {e}")
            
    if bucket_name:
        try:
            objs = s3_client.list_objects_v2(Bucket=bucket_name)
            for obj in objs.get('Contents', []):
                s3_client.delete_object(Bucket=bucket_name, Key=obj['Key'])
            s3_client.delete_bucket(Bucket=bucket_name)
            print(f"[*] Deleted temporary bucket {bucket_name}")
        except Exception as e:
            pass
