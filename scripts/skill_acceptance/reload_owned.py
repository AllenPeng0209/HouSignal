"""Reload only the project's PID-recorded local API/worker after idle checks."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.config import settings

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {'NATIVE_VIDEO_BACKEND','NATIVE_VIDEO_BASE_URL','NATIVE_VIDEO_MODEL','NATIVE_VIDEO_TIMEOUT_SECONDS',
           'NATIVE_WHISPER_MODEL_PATH','NATIVE_BACKGROUND_MODEL_PATH','NATIVE_TEXT_BACKEND',
           'NATIVE_TEXT_MODEL_PATH','NATIVE_TEXT_MODEL','NATIVE_TEXT_MAX_CONCURRENCY',
           'NATIVE_TEXT_MAX_CONTEXT_TOKENS','NATIVE_TEXT_TIMEOUT_SECONDS','NATIVE_LOCAL_TEXT_DAILY_TOKEN_BUDGET',
           'NATIVE_RESIDENT_TEXT_BASE_URL','NATIVE_RESIDENT_TEXT_MODEL','NATIVE_RESIDENT_TEXT_TOKEN_PATH',
           'NATIVE_RESIDENT_TEXT_TIMEOUT_SECONDS'}


def global_pending_jobs(db):
    """A policy-filtered zero cannot prove that all tenants are idle."""
    unavailable = '当前连接无法全局检查运行任务'
    try:
        if db.dialect.name == 'postgresql':
            db.execute(text('SET TRANSACTION READ ONLY'))
            if db.execute(text('SHOW transaction_read_only')).scalar() != 'on':
                raise RuntimeError(unavailable)
            role = db.execute(text("""WITH target AS (
                    SELECT oid, relkind FROM pg_class WHERE oid = to_regclass('public.jobs')
                ), applicable AS (
                    SELECT policy.* FROM pg_policy AS policy JOIN target ON policy.polrelid = target.oid
                    WHERE policy.polcmd IN ('r', '*') AND EXISTS (
                        SELECT 1 FROM unnest(policy.polroles) AS allowed(role_oid)
                        WHERE CASE WHEN allowed.role_oid = 0 THEN true
                            ELSE pg_has_role(current_user, allowed.role_oid, 'USAGE') END)
                )
                SELECT rolsuper, rolbypassrls,
                has_table_privilege(current_user, to_regclass('public.jobs'), 'SELECT') AS can_select_jobs,
                EXISTS (SELECT 1 FROM target WHERE relkind = 'r') AS base_jobs_table,
                EXISTS (SELECT 1 FROM applicable WHERE polpermissive
                    AND pg_get_expr(polqual, polrelid) = 'true') AS globally_permissive_select,
                NOT EXISTS (SELECT 1 FROM applicable WHERE NOT polpermissive) AS no_restrictive_select
                FROM pg_roles WHERE rolname = current_user""")).mappings().first()
            if (role is None or role.get('can_select_jobs') is not True or role.get('base_jobs_table') is not True
                    or not (role.get('rolsuper') is True or role.get('rolbypassrls') is True
                        or (role.get('globally_permissive_select') is True
                            and role.get('no_restrictive_select') is True))):
                raise RuntimeError(unavailable)
        elif db.dialect.name != 'sqlite':
            raise RuntimeError(unavailable)
        table = 'public.jobs' if db.dialect.name == 'postgresql' else 'jobs'
        pending = db.execute(text(f"SELECT count(*) FROM {table} WHERE status IN ('queued','running')")).scalar()
    except Exception:
        raise RuntimeError(unavailable) from None
    if type(pending) is not int or pending < 0:
        raise RuntimeError(unavailable)
    return pending


def _worker_fallback_url(primary):
    """No admin credentials: only the existing worker at the same owned endpoint."""
    try:
        requested = make_url(primary)
        worker = make_url(settings.worker_database_url)
        for url in (requested, worker):
            if (url.drivername not in {'postgresql', 'postgresql+psycopg', 'postgresql+psycopg2'}
                    or (url.host, url.port, url.database) != ('127.0.0.1', 55432, 'housignal')
                    or url.query):
                return None
        if (requested.username not in {'housignal_owner', 'housignal_app', 'housignal_worker'}
                or worker.username != 'housignal_worker' or worker == requested):
            return None
    except Exception:
        return None
    return settings.worker_database_url


def _pending_for_url(url):
    try:
        engine = create_engine(url, hide_parameters=True)
        try:
            with engine.connect() as db:
                return global_pending_jobs(db)
        finally:
            engine.dispose()
    except Exception:
        raise RuntimeError('当前连接无法全局检查运行任务') from None


def configured_pending_jobs():
    primary = settings.migration_database_url or settings.worker_database_url or settings.database_url
    try:
        return _pending_for_url(primary)
    except RuntimeError:
        worker = _worker_fallback_url(primary)
        if worker is None:
            raise
    return _pending_for_url(worker)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', nargs='*', type=Path, default=[])
    args = parser.parse_args()
    pending = configured_pending_jobs()
    if pending != 0:
        raise RuntimeError('Active jobs must finish before reloading services')
    python = str(ROOT/'.venv/bin/python')
    owned = [('worker','app.worker',[python,'-m','app.worker']),
             ('api','uvicorn app.main:app',[python,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8017'])]
    snapshots = {}
    for name, marker, _ in owned:
        path = ROOT/'.runtime'/f'{name}.pid'
        pid = int(path.read_text().strip())
        command = subprocess.check_output(['ps','-p',str(pid),'-o','command='], text=True).strip()
        assert command.startswith(python+' ') and marker in command, 'Unexpected process owner'
        snapshots[name] = pid
    updates = {}
    for path in args.config:
        data = {key.upper():value for key,value in json.loads(path.read_text()).items()}
        data = {key:value for key,value in data.items() if key not in {'MODEL_REPOSITORY','MODEL_REVISION','PROOF_FILE'}}
        assert set(data) <= ALLOWED, 'Only explicit non-secret native model configuration is supported'
        for key, value in data.items():
            assert isinstance(value,(str,int)) and not any(c in str(value) for c in '\r\n\0')
            updates[key] = str(value)
    if updates:
        env_path = ROOT/'.env'
        old = env_path.read_text()
        lines = [line for line in old.splitlines() if not any(re.match(r'\s*'+re.escape(key)+r'\s*=',line) for key in updates)]
        lines.extend(key+'='+json.dumps(value,ensure_ascii=False) for key,value in updates.items())
        env_path.write_text('\n'.join(lines)+'\n')
        env_path.chmod(0o600)
    for name, marker, command in owned:
        path = ROOT/'.runtime'/f'{name}.pid'
        assert int(path.read_text().strip()) == snapshots[name], 'A concurrent reload changed service ownership'
        os.kill(snapshots[name],signal.SIGTERM)
        for _ in range(50):
            try:
                os.kill(snapshots[name],0)
            except ProcessLookupError:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Owned process did not stop')
        with (ROOT/'.runtime'/f'{name}.log').open('ab') as log:
            process = subprocess.Popen(command,cwd=ROOT,env=dict(os.environ,PYTHONPATH=str(ROOT/'apps/api')),
                                       stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        path.write_text(str(process.pid)); path.chmod(0o600)
    for _ in range(50):
        try:
            response=httpx.get('http://127.0.0.1:8017/api/health',timeout=2)
            response.raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(.2)
    else:
        raise RuntimeError('API health did not recover')
    paths=httpx.get('http://127.0.0.1:8017/api/openapi.json',timeout=10).json()['paths']
    assert '/api/projects/{pid}/operations/skills/{skill_id}/preflight' in paths
    print(json.dumps({'api_health':'ok','worker_reloaded':True,'configuration_fields':sorted(updates)},ensure_ascii=False))


if __name__ == '__main__':
    main()
