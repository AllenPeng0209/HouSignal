"""Preparation binds an actual session; account switches invalidate it."""
import pytest
from fastapi import HTTPException
from test_platform_accounts import account_client, approve_fixture, BASE


def test_prepared_content_and_manifest_bind_the_session_selected_at_enqueue(account_client,monkeypatch):
    env=account_client
    approve_fixture(env)
    state={'session_id':'a'*64,'loggedIn':True,'verified':True,'state':'success'}
    monkeypatch.setattr(env.module.runtime,'login_status',lambda *args:dict(state))
    response=env.client.post(BASE+'/douyin/prepare',json={'content_version_id':'content'})
    assert response.status_code==200,response.text
    from app.job_models import Job
    job=env.db.get(Job,response.json()['job_id'])
    assert job.payload['session_id']=='a'*64
    result=env.module.handle_prepare(env.db,job)
    assert result['session_id']=='a'*64
    state['session_id']='b'*64
    with pytest.raises(HTTPException) as changed:
        env.module.handle_prepare(env.db,job)
    assert changed.value.status_code==409


def test_no_login_preparation_remains_unbound_and_cannot_claim_a_real_account(account_client):
    env=account_client
    approve_fixture(env)
    response=env.client.post(BASE+'/douyin/prepare',json={'content_version_id':'content'})
    from app.job_models import Job
    result=env.module.handle_prepare(env.db,env.db.get(Job,response.json()['job_id']))
    assert result['session_id'] is None
    assert result['account']['verified'] is False
