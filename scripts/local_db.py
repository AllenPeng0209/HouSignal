"""Start the project's private Postgres cluster. Never resets existing data."""
from pathlib import Path
import getpass
import os
import socket
import subprocess
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
DATA = RUNTIME / 'postgres'
SOCKETS = RUNTIME / 'pg_socket'
BIN = Path(os.environ.get('POSTGRES_BIN', '/opt/homebrew/opt/postgresql@17/bin'))


def run(*args):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('PostgreSQL command failed: ' + result.stderr[-1200:])


def secure_runtime_paths():
    for directory in (RUNTIME, SOCKETS):
        directory.mkdir(exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    for name in ('api.log', 'worker.log', 'postgres.log'):
        log = RUNTIME / name
        log.touch(mode=0o600, exist_ok=True)
        log.chmod(0o600)


def main():
    env = dotenv_values(ROOT / '.env')
    if not env.get('DATABASE_URL') or not env.get('MIGRATION_DATABASE_URL'):
        raise RuntimeError('Set local database URLs in .env first')
    secure_runtime_paths()
    if not (DATA / 'PG_VERSION').exists():
        with socket.socket() as check:
            if check.connect_ex(('127.0.0.1', 55432)) == 0:
                raise RuntimeError('Port 55432 is occupied; no existing service was modified')
        run(BIN / 'initdb', '-D', DATA, '--encoding=UTF8', '--locale=C', '--auth-local=trust', '--auth-host=scram-sha-256')
    status = subprocess.run([str(BIN / 'pg_ctl'), '-D', str(DATA), 'status'], capture_output=True)
    if status.returncode:
        run(BIN / 'pg_ctl', '-D', DATA, '-l', RUNTIME / 'postgres.log', '-o', f'-p 55432 -h 127.0.0.1 -k {SOCKETS}', '-w', 'start')
    with psycopg.connect(host=str(SOCKETS), port=55432, dbname='postgres', user=getpass.getuser(), autocommit=True) as conn:
        for field in ['MIGRATION_DATABASE_URL', 'DATABASE_URL', 'WORKER_DATABASE_URL']:
            url = urlparse(env[field].replace('postgresql+psycopg:', 'postgresql:'))
            name = url.username
            if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (name,)).fetchone():
                conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS').format(sql.Identifier(name), sql.Literal(url.password)))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='housignal'").fetchone():
            conn.execute('CREATE DATABASE housignal OWNER housignal_owner')
    with psycopg.connect(host=str(SOCKETS), port=55432, dbname='housignal', user=getpass.getuser(), autocommit=True) as conn:
        conn.execute('GRANT USAGE ON SCHEMA public TO housignal_app, housignal_worker')
        conn.execute('GRANT CONNECT ON DATABASE housignal TO housignal_app, housignal_worker')
    print('Project PostgreSQL ready on 127.0.0.1:55432; existing data preserved')


if __name__ == '__main__':
    main()
