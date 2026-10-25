"""Dedicated off-host copy. No existing object overwrite/delete; no key/value output."""
import hashlib,json,sys
from pathlib import Path
import boto3
from botocore.config import Config

def main():
    assert len(sys.argv)==2
    p=Path(sys.argv[1]);assert p.parent==Path('/run/backups') and p.name.endswith('.dump') and not p.is_symlink()
    values=json.loads(Path('/run/production/preserved-config.json').read_bytes())['values']
    assert values['R2_BUCKET_PRIVATE']=='clothing-erp-private'
    assert values['R2_ENDPOINT_URL'].rstrip('/')=='https://f88d559f4f464d1056f85d54f5ad0c81.r2.cloudflarestorage.com'
    s3=boto3.client('s3',endpoint_url=values['R2_ENDPOINT_URL'],aws_access_key_id=values['R2_ACCESS_KEY_ID'],aws_secret_access_key=values['R2_SECRET_ACCESS_KEY'],region_name='auto',config=Config(signature_version='s3v4',connect_timeout=10,read_timeout=60,retries={'total_max_attempts':1}))
    size=p.stat().st_size;assert 0<size<=2*1024**3
    digest=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
    key='clothinkai-production-20261018/database-backups/'+p.name
    try:s3.head_object(Bucket='clothing-erp-private',Key=key)
    except s3.exceptions.ClientError as e:assert e.response['Error']['Code'] in ('404','NoSuchKey','NotFound')
    else:raise ValueError('existing_object')
    with p.open('rb') as f:
        s3.put_object(Bucket='clothing-erp-private',Key=key,Body=f,ContentLength=size,ContentType='application/octet-stream',Metadata={'sha256':digest},IfNoneMatch='*')
    response=s3.get_object(Bucket='clothing-erp-private',Key=key)
    assert response['ContentLength']==size and response['Metadata'].get('sha256')==digest
    h=hashlib.sha256();total=0
    try:
        while block:=response['Body'].read(1024**2):
            total+=len(block);assert total<=size;h.update(block)
    finally:response['Body'].close()
    assert total==size and h.hexdigest()==digest
    print(json.dumps({'offhost_verified':True,'bytes':size,'sha256':digest,'existing_objects_changed':False}))

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'status':'BACKUP_UPLOAD_STOP','type':type(e).__name__}));sys.exit(1)
