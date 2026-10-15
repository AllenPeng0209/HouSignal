"""Stop only matching project-owned API/Worker processes. Keep database/data."""
from pathlib import Path
import os
import signal
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def main():
    sys.path[:0]=[str(ROOT),str(ROOT/'apps/api')]
    from scripts.skill_acceptance.reload_owned import configured_pending_jobs
    if configured_pending_jobs()!=0:
        raise RuntimeError('Active jobs must finish before stopping services; current tasks are preserved')
    for name in ['api','worker']:
        p=ROOT/'.runtime'/(name+'.pid')
        if not p.is_file():continue
        try:
            pid=int(p.read_text())
            command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
            if str(ROOT/'.venv/bin/python') in command and ('app.main:app' in command or 'app.worker' in command):
                os.killpg(pid,signal.SIGTERM)
                p.unlink()
        except (ValueError,ProcessLookupError,subprocess.CalledProcessError):pass
    print('Owned application processes stopped; database and files preserved')

if __name__=='__main__':main()
