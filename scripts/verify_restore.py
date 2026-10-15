"""Restore into a new owned verification database; never touches live data."""
from datetime import datetime, timezone
from pathlib import Path
import getpass
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
import psycopg
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]


def main():
    backup=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else sorted((ROOT/'.runtime/backups').glob('*.zip'))[-1]
    if not backup.is_relative_to(ROOT/'.runtime/backups'):raise SystemExit('Use an owned private project backup')
    name='housignal_restore_'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    with zipfile.ZipFile(backup) as archive, tempfile.TemporaryDirectory(dir=ROOT/'.runtime') as temp:
        manifest=json.loads(archive.read('manifest.json'))
        for file in manifest['files']:
            if hashlib.sha256(archive.read(file['path'])).hexdigest()!=file['sha256']:raise RuntimeError('Backup checksum mismatch')
        dump=Path(temp)/'database.dump';dump.write_bytes(archive.read('database.dump'))
        options={'host':str(ROOT/'.runtime/pg_socket'),'port':55432,'user':getpass.getuser()}
        with psycopg.connect(**options,dbname='postgres',autocommit=True) as db:db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        subprocess.run(['/opt/homebrew/opt/postgresql@17/bin/pg_restore','-h',options['host'],'-p','55432','-U',options['user'],'--no-owner','--no-acl','-d',name,str(dump)],check=True,capture_output=True)
        with psycopg.connect(**options,dbname=name) as db:
            tables=db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
            version=db.execute('SELECT version_num FROM alembic_version').fetchone()[0]
            withdrawn=db.execute("SELECT count(*) FROM leads WHERE stage='withdrawn'").fetchone()[0]
        report={'restored_database':name,'tables':tables,'schema_version':version,'file_checksums_verified':len(manifest['files']),'withdrawn_records_preserved':withdrawn,'live_database_modified':False}
        report_path = ROOT/'reports/restore-verification.json'
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
