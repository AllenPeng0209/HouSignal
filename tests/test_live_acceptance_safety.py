"""Acceptance reports distinguish attempted admission from actual worker jobs."""
import importlib.util
from pathlib import Path

import httpx
import pytest


def live_module():
    path = Path(__file__).parents[1] / 'scripts/skill_acceptance/run_live.py'
    spec = importlib.util.spec_from_file_location('live_acceptance_recovery', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_temporary_api_restart_resumes_same_job_read_without_submitting_another_job(monkeypatch):
    module=live_module(); count=[0]; clock=[0.0]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def boundary(request):
        count[0]+=1
        if count[0] < 3:raise httpx.ConnectError('temporary restart',request=request)
        return httpx.Response(200,json={'id':'original-job','status':'succeeded'})
    with httpx.Client(base_url='http://127.0.0.1:8017',transport=httpx.MockTransport(boundary)) as client:
        result=module.request(client,'GET','/api/jobs/original-job')
    assert result=={'id':'original-job','status':'succeeded'} and count[0]==3


def test_ambiguous_run_post_timeout_is_never_repeated():
    module=live_module(); created=[0]
    def boundary(request):
        created[0]+=1
        raise httpx.ReadTimeout('response lost after enqueue',request=request)
    with httpx.Client(base_url='http://127.0.0.1:8017',transport=httpx.MockTransport(boundary)) as client:
        with pytest.raises(httpx.ReadTimeout):module.request(client,'POST','/api/projects/p/operations/skills/native:copywriting/run',json={'input':'草稿'})
    assert created[0]==1


def test_api_recovery_wait_has_a_deadline(monkeypatch):
    module=live_module();clock=[0.0]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def boundary(request):raise httpx.ConnectError('offline',request=request)
    with httpx.Client(base_url='http://127.0.0.1:8017',transport=httpx.MockTransport(boundary)) as client:
        with pytest.raises(httpx.ConnectError):module.request(client,'GET','/api/jobs/original-job')
    assert 55 <=clock[0]<=60


def test_quota_rejection_before_enqueue_is_not_an_actual_skill_execution():
    path = Path(__file__).parents[1] / 'scripts/skill_acceptance/run_live.py'
    spec = importlib.util.spec_from_file_location('live_acceptance_safety', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def admission(request):
        if request.url.path.endswith('/preflight'):
            return httpx.Response(200, json={'status': 'ready', 'reason': 'local model configured', 'execution_engine': 'housignal_text'})
        return httpx.Response(429, json={'detail': '团队今日模型用量已达到限制'})
    state = {'project_id': 'p', 'accounts': {'primary': 'a'}, 'assets': {}}
    case = {'case_id': 'copy', 'skill_name': 'copywriting', 'billing': 'free', 'input': '写一段草稿。'}
    with httpx.Client(base_url='http://127.0.0.1:8017', transport=httpx.MockTransport(admission)) as client:
        result = module.run_case(client, state, case, [{'skill_name': 'copywriting', 'id': 'native:copywriting'}], False)
    assert result['status'] == 'failed' and result['submission_attempted'] is True
    assert result['actual_attempt'] is False and 'job_id' not in result
