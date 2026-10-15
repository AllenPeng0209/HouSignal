"""Service maintenance must not interrupt durable content production."""
import runpy
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.mark.parametrize('pending',[1,None])
def test_service_stop_checks_global_queue_before_signals_or_pid_changes(tmp_path,monkeypatch,pending):
    root=tmp_path/'project';(root/'scripts').mkdir(parents=True);(root/'.runtime').mkdir()
    script=root/'scripts/stop_local.py'
    script.write_text((Path(__file__).resolve().parents[1]/'scripts/stop_local.py').read_text())
    for name in ('api','worker'):(root/'.runtime'/f'{name}.pid').write_text('101')
    helper=ModuleType('scripts.skill_acceptance.reload_owned')
    def queue():
        if pending is None:raise RuntimeError('Cannot verify the global queue')
        return pending
    helper.configured_pending_jobs=queue
    monkeypatch.setitem(sys.modules,'scripts.skill_acceptance.reload_owned',helper)
    monkeypatch.setattr(sys,'path',list(sys.path))
    monkeypatch.setattr('subprocess.check_output',lambda *_a,**_k:str(root/'.venv/bin/python')+' -m app.worker')
    signals=[];monkeypatch.setattr('os.killpg',lambda *args:signals.append(args))
    with pytest.raises(RuntimeError):runpy.run_path(str(script),run_name='__main__')
    assert not signals
    assert all((root/'.runtime'/f'{name}.pid').read_text()=='101' for name in ('api','worker'))
