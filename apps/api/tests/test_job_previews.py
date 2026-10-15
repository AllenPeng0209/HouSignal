"""Partial answers have the same project, source and consent guard as history."""
from datetime import datetime, timedelta, timezone
import asyncio
import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.core_models import Fact, LoginSession, Membership, Property, Tenant, User
from app.job_models import Job
from app.jobs import router
from app.security import context_for_user, get_context
import app.jobs as jobs_module
from starlette.requests import Request
import app.business_models  # register all history/provenance tables
import app.agent_models


@pytest.fixture
def preview_app():
    engine = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine,expire_on_commit=False)
    db.add_all([Tenant(id='t',name='示例团队'),User(id='u',email='test@example.test',name='测试',password_hash='unused')])
    db.flush()
    db.add_all([Membership(user_id='u',tenant_id='t',role='admin'),Property(id='p',tenant_id='t',name='示例项目',city='上海')])
    db.flush()
    db.add(Fact(id='f',tenant_id='t',property_id='p',semantic_key='area',label='建筑面积',value='329',unit='㎡',status='verified',revision=3,source='示例资料'))
    db.add(Job(id='j',tenant_id='t',property_id='p',user_id='u',kind='chat',status='running',progress=.5,result={'preview_text':'建筑面积为329㎡[S1]。','access_refs':[{'kind':'fact','id':'f','label':'建筑面积','revision':3,'status':'verified','valid':True}]}))
    db.add(LoginSession(id='s',token_hash=hashlib.sha256(b'job-preview-test').hexdigest(),user_id='u',tenant_id='t',expires_at=datetime.now(timezone.utc)+timedelta(hours=1)))
    db.commit()
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_context] = lambda: context_for_user(db,'u','t')
    with TestClient(app) as client: yield client,db
    db.close();engine.dispose()


def test_valid_preview_visible_without_private_access_reference_payload(preview_app):
    client,db = preview_app
    response = client.get('/api/jobs/j');assert response.status_code==200
    assert response.json()['result']['preview_text']=='建筑面积为329㎡[S1]。'
    assert 'access_refs' not in response.json()['result']


def test_fact_change_masks_partial_answer_immediately(preview_app):
    client,db = preview_app
    fact = db.get(Fact,'f');fact.revision=4;fact.value='330';db.commit()
    response = client.get('/api/jobs/j');assert response.status_code==200
    assert not response.json()['result'].get('preview_text')
    assert 'access_refs' not in response.json()['result']


def test_partial_answer_without_source_provenance_is_not_returned(preview_app):
    client,db = preview_app
    job = db.get(Job,'j');job.result={'preview_text':'未查来源的客户判断'};db.commit()
    assert not client.get('/api/jobs/j').json()['result'].get('preview_text')


def test_cancel_discards_preview_and_internal_provenance(preview_app):
    client,db = preview_app
    response = client.post('/api/jobs/j/cancel');assert response.status_code==200
    assert response.json()['status']=='cancelled'
    assert not response.json()['result'].get('preview_text')
    assert 'access_refs' not in response.json()['result']


def event_response(db, monkeypatch):
    engine = db.get_bind()
    monkeypatch.setattr(jobs_module,'SessionLocal',lambda: Session(engine,expire_on_commit=False))
    async def receive(): return {'type':'http.request','body':b''}
    request = Request({'type':'http','headers':[(b'cookie',b'hs_session=job-preview-test')],'method':'GET','path':'/api/jobs/j/events'},receive)
    return jobs_module.job_events('j',request,context_for_user(db,'u','t'),db)


def test_open_stream_stops_when_login_expires(preview_app,monkeypatch):
    _,db = preview_app
    response = event_response(db,monkeypatch)
    async def check():
        events = response.body_iterator
        first = await asyncio.wait_for(anext(events),1)
        assert '建筑面积为329' in first
        db.get(LoginSession,'s').expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        db.get(Job,'j').progress=.6;db.commit()
        second = await asyncio.wait_for(anext(events),1)
        assert 'event: error' in second
        assert '建筑面积为329' not in second
        await events.aclose()
    asyncio.run(check())
