"""Host timer: PG18 custom full database, archive checks, off-host verification. No deletion."""
import datetime,fcntl,hashlib,json,os,stat,subprocess,sys,uuid
from pathlib import Path
ROOT=Path('/srv/clothinkai/production-20261018')
DB='clothinkai-prod-db-20261018'
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','DOCKER_HOST':'unix:///var/run/docker.sock','DOCKER_CONFIG':str(ROOT/'docker-client'),'LC_ALL':'C'}
def run(args,timeout=60):
    r=subprocess.run(args,capture_output=True,timeout=timeout,env=ENV)
    assert r.returncode==0 and len(r.stdout)<4*1024**2
    return r.stdout
def main():
    assert os.geteuid()==0 and Path('/var/lib/cloud/data/instance-id').read_text().strip()=='i-bp13rq87f4kzz3aalaf7'
    run(['/usr/local/sbin/clothinkai-verify-data-mount'])
    folder=ROOT/'backups';s=folder.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o700
    # Preserve all prior backups; fail visibly before quota exhaustion instead of deleting.
    assert sum(p.stat().st_size for p in folder.iterdir() if p.is_file())<10*1024**3
    with (folder/'backup.lock').open('a+b') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        name=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex+'.dump'
        partial=folder/(name+'.partial');final=folder/name
        fd=os.open(partial,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o600)
            r=subprocess.run(['/usr/bin/timeout','--kill-after=5s','600','/usr/bin/prlimit','--fsize=2147483648','--','docker','exec','-u','0','-e','PGOPTIONS=-c default_transaction_read_only=on -c lock_timeout=10000',DB,'pg_dump','--format=custom','--lock-wait-timeout=10s','--host=/var/run/postgresql','--username=root','--dbname=clothing_erp_prod_20261018'],stdout=f,stderr=subprocess.PIPE,timeout=610,env=ENV)
            assert r.returncode==0;f.flush();os.fsync(f.fileno())
        with partial.open('rb') as f:assert f.read(5)==b'PGDMP'
        run(['docker','exec','-u','0',DB,'pg_restore','--list','/backups/'+partial.name],120)
        run(['docker','exec','-u','0',DB,'pg_restore','--file=/dev/null','/backups/'+partial.name],120)
        digest=hashlib.file_digest(partial.open('rb'),'sha256').hexdigest();size=partial.stat().st_size
        os.link(partial,final);partial.unlink()
        out=json.loads(run(['docker','exec','-u','0','clothinkai-prod-api-20261018','python','/opt/production/upload_backup.py','/run/backups/'+name],900))
        assert out['offhost_verified'] and out['sha256']==digest and out['bytes']==size
        with (folder/(name+'.json')).open('x',encoding='utf-8') as f:
            os.chmod(f.name,0o600);json.dump({'archive_list':True,'archive_full_read':True,**out},f)
        print(json.dumps({'status':'PRODUCTION_BACKUP_VERIFIED','bytes':size,'local_and_offhost':True,'retention_deletion_enabled':False}))
if __name__=='__main__':
    os.umask(0o077)
    try:main()
    except Exception as e:
        print(json.dumps({'status':'PRODUCTION_BACKUP_STOP','type':type(e).__name__,'state_retained':True}));sys.exit(1)
