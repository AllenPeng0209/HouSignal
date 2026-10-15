"""Core behavior contracts: credentials, tenancy, fact proof and private media."""
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.core_models import User, Tenant, Membership, Property, Fact, FactRevision, Asset, LoginSession
from app.security import Context, context_for_user, require_project, hash_password, verify_password, encrypt_pii, decrypt_pii
from app.core import router, valid_fact
from app.config import settings
from app.job_models import Job
import app.core as core_module


@pytest.fixture
def core_env(tmp_path, monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t1', name='云境团队'), Tenant(id='t2', name='另一团队')])
    db.add_all([User(id='u1', email='owner@example.test', name='负责人', password_hash=hash_password('strong-password-123'), active=True),
                User(id='u2', email='editor@example.test', name='编辑', password_hash=hash_password('strong-password-123'), active=True)])
    db.add_all([Membership(id='m1', user_id='u1', tenant_id='t1', role='admin', project_ids=None, active=True),
                Membership(id='m2', user_id='u2', tenant_id='t1', role='editor', project_ids=['p1'], active=True)])
    db.add_all([Property(id='p1', tenant_id='t1', name='云境一号', city='上海', property_type='豪宅'),
                Property(id='p2', tenant_id='t1', name='栖岸', city='杭州', property_type='豪宅'),
                Property(id='p3', tenant_id='t2', name='外部项目', city='苏州', property_type='豪宅')])
    db.add(Fact(id='f1', tenant_id='t1', property_id='p1', semantic_key='area', label='建筑面积', value='', unit='㎡', source='', status='unverified', revision=1, required=True))
    db.commit()
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, db
    db.close()
    engine.dispose()


def login(client, email='owner@example.test'):
    response = client.post('/api/auth/login', json={'email':email,'password':'strong-password-123'})
    assert response.status_code == 200
    return response


def test_password_hash_and_pii_do_not_store_plaintext():
    hashed = hash_password('strong-password-123')
    assert hashed != 'strong-password-123'
    assert verify_password('strong-password-123', hashed)
    assert not verify_password('incorrect', hashed)
    cipher = encrypt_pii('13800138000')
    assert '13800138000' not in cipher
    assert decrypt_pii(cipher) == '13800138000'


def test_opaque_session_login_and_logout(core_env):
    client, db = core_env
    assert client.get('/api/auth/me').status_code == 401
    response = login(client)
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'samesite=lax' in cookie.lower()
    raw_token = client.cookies.get('hs_session')
    session = db.scalar(select(LoginSession))
    assert session.token_hash != raw_token
    assert client.get('/api/auth/me').json()['role'] == 'admin'
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/auth/me').status_code == 401


def test_project_scope_and_cross_tenant_access_are_hidden(core_env):
    client, db = core_env
    login(client, 'editor@example.test')
    assert [x['id'] for x in client.get('/api/projects').json()] == ['p1']
    assert client.get('/api/projects/p2').status_code == 404
    assert client.get('/api/projects/p3').status_code == 404
    assert client.post('/api/projects', json={'name':'越权创建','city':'上海'}).status_code == 403


def test_fact_verification_rejects_placeholder_and_requires_revision(core_env):
    client, db = core_env
    login(client)
    assert client.post('/api/projects/p1/facts/f1/verify', json={'confirmed':True}).status_code == 422
    changed = client.patch('/api/projects/p1/facts/f1', json={'value':'328','unit':'㎡','source':'实测报告第2页'})
    assert changed.status_code == 200
    assert changed.json()['revision'] == 2
    assert changed.json()['status'] == 'unverified'
    assert client.post('/api/projects/p1/facts/f1/verify', json={'confirmed':False}).status_code == 422
    verified = client.post('/api/projects/p1/facts/f1/verify', json={'confirmed':True})
    assert verified.status_code == 200 and verified.json()['status'] == 'verified'
    assert len(db.scalars(select(FactRevision)).all()) >= 2
    changed = client.patch('/api/projects/p1/facts/f1', json={'value':'待确认','unit':'㎡','source':'待上传'})
    assert changed.status_code == 200
    assert changed.json()['status'] == 'unverified'
    assert client.post('/api/projects/p1/facts/f1/verify', json={'confirmed':True}).status_code == 422


def test_private_file_route_requires_project_access(core_env):
    client, db = core_env
    login(client)
    upload = client.post('/api/projects/p2/assets', files={'file':('evidence.txt', io.BytesIO('真实楼盘资料'.encode()), 'text/plain')})
    assert upload.status_code == 200
    dto = upload.json()
    assert 'storage_key' not in dto
    assert dto['rights'] == 'pending'
    assert client.get(f"/api/projects/p2/assets/{dto['id']}/file").content == '真实楼盘资料'.encode()
    client.post('/api/auth/logout')
    assert client.get(f"/api/projects/p2/assets/{dto['id']}/file").status_code == 401
    login(client, 'editor@example.test')
    assert client.get(f"/api/projects/p2/assets/{dto['id']}/file").status_code == 404


def test_upload_rejects_executable_disguised_and_empty_files(core_env):
    client, _ = core_env
    login(client)
    assert client.post('/api/projects/p1/assets', files={'file':('payload.exe', b'MZunsafe', 'application/octet-stream')}).status_code == 422
    assert client.post('/api/projects/p1/assets', files={'file':('photo.jpg', b'not an image', 'image/jpeg')}).status_code == 422
    assert client.post('/api/projects/p1/assets', files={'file':('empty.txt', b'', 'text/plain')}).status_code == 422


def test_revoked_membership_immediately_ends_access(core_env):
    client, db = core_env
    login(client)
    db.get(Membership, 'm1').active = False
    db.commit()
    assert client.get('/api/projects').status_code == 403


def test_new_project_is_empty_and_requires_admin(core_env):
    client, db = core_env
    login(client)
    response = client.post('/api/projects',json={'name':'澜庭江岸','city':'苏州','owner_name':'唐主管'})
    assert response.status_code == 200
    pid = response.json()['id']
    assert response.json()['completeness']['percent'] == 14
    facts = client.get(f'/api/projects/{pid}/facts').json()
    assert len(facts) == 6 and all(f['value']=='' and f['status']=='unverified' for f in facts)
    assert client.get(f'/api/projects/{pid}/assets').json() == []


def test_fact_unit_and_expiry_are_required_for_verified_area(core_env):
    client, _ = core_env
    login(client)
    base = '/api/projects/p1/facts/f1'
    client.patch(base,json={'value':'328','unit':'','source':'实测报告第2页'})
    assert client.post(base+'/verify',json={'confirmed':True}).status_code == 422
    client.patch(base,json={'value':'328','unit':'㎡','source':'实测报告第2页','valid_until':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()})
    assert client.post(base+'/verify',json={'confirmed':True}).status_code == 422


def test_fact_verification_rejects_wrong_area_unit_and_placeholder_prefix(core_env):
    client, _ = core_env
    login(client)
    base = '/api/projects/p1/facts/f1'
    client.patch(base,json={'value':'328','unit':'米','source':'实测报告第2页'})
    assert client.post(base+'/verify',json={'confirmed':True}).status_code == 422
    client.patch(base,json={'value':'328','unit':'㎡','source':'待确认楼书第2页'})
    assert client.post(base+'/verify',json={'confirmed':True}).status_code == 422


@pytest.mark.parametrize('value',['NaN','Infinity','-1','0'])
def test_fact_measurement_must_be_positive_finite_number(core_env,value):
    client, _ = core_env
    login(client)
    client.patch('/api/projects/p1/facts/f1',json={'value':value,'unit':'㎡','source':'实测报告第2页'})
    assert client.post('/api/projects/p1/facts/f1/verify',json={'confirmed':True}).status_code == 422


def test_upload_size_and_path_escape_are_blocked(core_env,monkeypatch):
    client, db = core_env
    login(client)
    monkeypatch.setattr(core_module,'MAX_UPLOAD_BYTES',3)
    assert client.post('/api/projects/p1/assets',files={'file':('evidence.txt',b'four','text/plain')}).status_code == 413
    db.add(Asset(id='malicious-path',tenant_id='t1',property_id='p1',name='private.txt',kind='document',storage_key='../../private.txt',mime_type='text/plain',sha256='a'*64,byte_size=1))
    db.commit()
    assert client.get('/api/projects/p1/assets/malicious-path/file').status_code == 404


def test_model_processing_permission_is_explicit_and_revisioned(core_env):
    client, db = core_env
    login(client)
    asset = client.post('/api/projects/p1/assets',files={'file':('evidence.txt',b'328 square meters','text/plain')}).json()
    assert asset['metadata_json']['model_processing_authorized'] is False
    url = f"/api/projects/p1/assets/{asset['id']}/rights"
    assert client.post(url,json={'rights':'authorized','evidence':'待确认','model_processing_authorized':True}).status_code == 422
    ordinary = client.post(url,json={'rights':'authorized','evidence':'项目方授权书第3页'}).json()
    assert ordinary['rights_revision'] == 2
    assert ordinary['metadata_json']['model_processing_authorized'] is False
    assert len(db.scalars(select(Job)).all()) == 1
    with_model = client.post(url,json={'rights':'authorized','evidence':'项目方授权书第3页，许可模型处理','model_processing_authorized':True}).json()
    assert with_model['rights_revision'] == 3
    assert len(db.scalars(select(Job)).all()) == 2
    revoked = client.post(url,json={'rights':'revoked','evidence':'授权撤回'}).json()
    assert revoked['rights_revision'] == 4 and revoked['metadata_json']['model_processing_authorized'] is False


def test_member_management_immediately_enforces_role_and_project_scope(core_env):
    client, db = core_env
    login(client)
    response = client.post('/api/organization/members',json={'email':'sales@example.test','name':'销售顾问','password':'member-password-123','role':'sales','project_ids':['p1']})
    assert response.status_code == 200
    member = response.json()
    assert 'password' not in member and 'password_hash' not in member
    assert client.post('/api/organization/members',json={'email':'cross@example.test','name':'销售顾问','password':'member-password-123','role':'sales','project_ids':['p3']}).status_code == 422
    assert client.patch('/api/organization/members/m1',json={'active':False}).status_code == 422
    changed = client.patch('/api/organization/members/m2',json={'project_ids':['p2'],'role':'viewer'})
    assert changed.status_code == 200
    client.post('/api/auth/logout')
    login(client,'editor@example.test')
    assert [p['id'] for p in client.get('/api/projects').json()] == ['p2']
    assert client.get('/api/organization/members').status_code == 403
    assert client.patch('/api/projects/p2',json={'owner_name':'越权编辑'}).status_code == 403
