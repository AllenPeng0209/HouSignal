"""Start owned application processes with private logs and persistent data."""
from pathlib import Path
import os
import subprocess
import sys
import time
import urllib.request
from local_db import secure_runtime_paths

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'
PYTHON=ROOT/'.venv/bin/python'


def run(args):
    env={**os.environ,'PYTHONPATH':str(ROOT/'apps/api')}
    result=subprocess.run([str(x) for x in args],cwd=ROOT,env=env)
    if result.returncode:raise SystemExit(result.returncode)


def start(name,args):
    pid_file=RUNTIME/(name+'.pid')
    if pid_file.exists():
        try:
            pid=int(pid_file.read_text());os.kill(pid,0)
            command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
            if str(PYTHON) in command and ('app.main:app' in command or 'app.worker' in command):return
        except (ValueError,ProcessLookupError,subprocess.CalledProcessError):pass
    env={**os.environ,'PYTHONPATH':str(ROOT/'apps/api')}
    with open(RUNTIME/(name+'.log'),'a') as log:
        process=subprocess.Popen([str(x) for x in args],cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
    pid_file.write_text(str(process.pid));pid_file.chmod(0o600)


def main():
    if not PYTHON.is_file() or not (ROOT/'.env').is_file():
        raise SystemExit('Install Python dependencies and configure the private .env first')
    import shutil
    node=shutil.which('node')
    if not node or not (ROOT/'apps/agent-runtime/node_modules/@earendil-works/pi-agent-core/package.json').is_file():
        raise SystemExit('Install Node.js >=22.19.0, then run npm ci --prefix apps/agent-runtime for the Pi secretary runtime')
    secure_runtime_paths()
    run([PYTHON,ROOT/'scripts/local_db.py'])
    run([PYTHON,'-m','alembic','upgrade','head'])
    run([PYTHON,ROOT/'scripts/install_rls.py'])
    if '--seed' in sys.argv:run([PYTHON,ROOT/'scripts/seed_local.py'])
    if not (ROOT/'apps/web/dist/index.html').is_file():raise SystemExit('Build apps/web first')
    start('worker',[PYTHON,'-m','app.worker'])
    start('api',[PYTHON,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8017'])
    for _ in range(40):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8017/api/health',timeout=2) as response:
                if response.status==200:break
        except OSError:time.sleep(.25)
    else:raise SystemExit('Application did not start; inspect .runtime/api.log')
    print('Louxiang application running: http://127.0.0.1:8017/\nStop application processes with scripts/stop_local.py. Database data is preserved.')


if __name__=='__main__':main()
