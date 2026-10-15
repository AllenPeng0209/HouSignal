"""Private local backup, without credentials. Reads the owned local cluster."""
from datetime import datetime, timezone
from pathlib import Path
import getpass
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from app.config import settings

ROOT=Path(__file__).resolve().parents[1]


def main():
    directory=ROOT/'.runtime/backups';directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
    output=directory/('housignal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.zip')
    with tempfile.TemporaryDirectory(dir=ROOT/'.runtime') as temp:
        dump=Path(temp)/'database.dump'
        subprocess.run(['/opt/homebrew/opt/postgresql@17/bin/pg_dump','-h',str(ROOT/'.runtime/pg_socket'),'-p','55432','-U',getpass.getuser(),'-d','housignal','-Fc','-f',str(dump)],check=True,capture_output=True)
        files=[(dump,'database.dump')]+[(p,'storage/'+str(p.relative_to(settings.storage_root))) for p in settings.storage_root.rglob('*') if p.is_file()]
        manifest={'created_at':datetime.now(timezone.utc).isoformat(),'files':[{'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p,name in files],'credentials_included':False}
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for p,name in files:archive.write(p,name)
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    output.chmod(0o600)
    print(json.dumps({'backup':str(output),'private_mode':'0600','files':len(files),'credentials_included':False},ensure_ascii=False))


if __name__=='__main__':main()
