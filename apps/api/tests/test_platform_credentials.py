"""Official WeChat credentials stay scoped, encrypted, and out of login/model state."""
import secrets
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

BASE = '/api/projects/p/operations/platform-accounts/wechat-oa/credentials'
APP_ID = 'wx0123456789abcdef'
SECRET = secrets.token_hex(16)


@pytest.fixture
def credentials_client(tmp_path, monkeypatch):
    from app.db import Base, get_db
    from app.core_models import Tenant, User, Property
    from app.security import Context, get_context
    from app.platform_runtime import PlatformRuntime
    module = None
    if importlib.util.find_spec('app.platform_credentials'):
        from app import platform_credentials as module
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([Tenant(id='t', name='团队'), Tenant(id='else', name='其它团队'), User(id='u', email='test@x.cn', password_hash='unused', name='编辑', active=True)])
    db.flush()
    db.add_all([Property(id=pid, tenant_id=tenant, name=pid, city='苏州', channel='微信公众号') for pid, tenant in [('p', 't'), ('p2', 't'), ('foreign', 'else')]])
    db.commit()
    context = {'value': Context('u', 't', 'content', '编辑', 'test@x.cn', None)}
    runtime = PlatformRuntime(tmp_path)
    records = []
    payload = {'value': {'access_token': 'private-issued-token', 'expires_in': 7200}, 'status': 200}
    def wechat(request):
        records.append(request)
        return httpx.Response(payload['status'], json=payload['value'])
    transport = httpx.MockTransport(wechat)
    if module:
        monkeypatch.setattr(module, 'runtime', runtime)
        native_client = httpx.Client
        monkeypatch.setattr(module, '_http_client', lambda: native_client(transport=transport, timeout=10, follow_redirects=False, trust_env=False))
    app = FastAPI()
    if module:
        app.include_router(module.router, prefix='/api/projects/{pid}/operations/platform-accounts')
    app.dependency_overrides[get_context] = lambda: context['value']
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, module=module, root=tmp_path, runtime=runtime, records=records, payload=payload, context=context)
    db.close()
    engine.dispose()


def body(**updates):
    return {'appId': APP_ID, 'appSecret': SECRET, 'name': '住宅公众号', 'author': '内容团队', **updates}


def test_unconfigured_credentials_are_local_metadata_without_network(credentials_client):
    env = credentials_client
    response = env.client.get(BASE)
    assert response.status_code == 200
    assert response.json()['configured'] is False
    assert response.json()['verified'] is False
    assert not env.records
    assert not (env.root / 'runtime').exists()


def test_success_encrypts_scoped_credentials_and_does_not_claim_browser_login(credentials_client):
    env = credentials_client
    response = env.client.post(BASE, json=body())
    assert response.status_code == 200
    value = response.json()
    assert value['verified'] is True
    assert value['configured'] is True
    assert value['mode'] == 'official_api'
    assert value['capabilities'] == 'not_checked'
    assert value['appIdMasked'] == 'wx0123***cdef'
    assert 'loggedIn' not in value
    assert SECRET not in response.text and 'private-issued-token' not in response.text
    scope = env.runtime._scope('t', 'p', 'wechat-oa')
    path = scope / 'credentials' / 'config.json'
    stored = path.read_text()
    assert SECRET not in stored and APP_ID not in stored and 'private-issued-token' not in stored
    from app.security import decrypt_pii
    saved = json.loads(stored)
    assert decrypt_pii(saved['appSecret_cipher']) == SECRET
    assert oct(path.stat().st_mode & 0o777) == '0o600'
    assert oct(path.parent.stat().st_mode & 0o777) == '0o700'
    assert not (scope / 'status.json').exists()
    assert env.client.get(BASE.replace('/p/', '/p2/')).json()['configured'] is False
    request = env.records[0]
    assert str(request.url) == 'https://api.weixin.qq.com/cgi-bin/stable_token'
    assert request.method == 'POST'
    assert json.loads(request.content) == {'grant_type': 'client_credential', 'appid': APP_ID, 'secret': SECRET, 'force_refresh': False}


@pytest.mark.parametrize('code', [40164, 40125, 40013])
def test_api_failure_is_saved_unverified_with_numeric_code_only(credentials_client, code):
    env = credentials_client
    env.payload['value'] = {'errcode': code, 'errmsg': f'private token={SECRET} rid=private-id'}
    response = env.client.post(BASE, json=body())
    assert response.status_code == 200
    assert response.json()['configured'] is True
    assert response.json()['verified'] is False
    assert response.json()['code'] == code
    assert SECRET not in response.text and 'private-id' not in response.text
    assert env.client.get(BASE).json()['verified'] is False


@pytest.mark.parametrize('role,status', [('sales', 403), ('customer', 403), ('content', 200), ('admin', 200)])
def test_credentials_writes_require_project_content_permission(credentials_client, role, status):
    env = credentials_client
    env.context['value'] = replace(env.context['value'], role=role)
    response = env.client.post(BASE, json=body())
    assert response.status_code == status
    assert len(env.records) == (1 if status == 200 else 0)


def test_unknown_platform_and_cross_project_access_do_not_contact_wechat(credentials_client):
    env = credentials_client
    assert env.client.post(BASE.replace('/wechat-oa/', '/douyin/'), json=body()).status_code == 404
    assert env.client.get(BASE.replace('/p/', '/foreign/')).status_code == 404
    env.context['value'] = replace(env.context['value'], project_ids=['p2'])
    assert env.client.post(BASE, json=body()).status_code == 404
    assert not env.records


@pytest.mark.parametrize('values', [body(appSecret=''), body(appId='wrong'), body(appSecret={'secret': SECRET}), body(extra=SECRET)])
def test_bad_payload_never_reflects_secrets_or_issues_network_request(credentials_client, values):
    env = credentials_client
    response = env.client.post(BASE, json=values)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert not env.records


def test_symlinked_credentials_directory_is_rejected_without_writing_target(credentials_client, tmp_path):
    env = credentials_client
    target = tmp_path / 'outside'
    target.mkdir()
    scope = env.runtime._scope('t', 'p', 'wechat-oa', create=True)
    (scope / 'credentials').symlink_to(target, target_is_directory=True)
    response = env.client.post(BASE, json=body())
    assert response.status_code == 409
    assert not list(target.iterdir())
    assert not env.records


def test_redirect_and_malformed_token_never_verify_credentials(credentials_client):
    env = credentials_client
    env.payload['status'] = 302
    env.payload['value'] = {'access_token': 'private-issued-token', 'expires_in': 7200}
    result = env.client.post(BASE, json=body()).json()
    assert result['verified'] is False
    assert len(env.records) == 1
    env.payload['status'] = 200
    env.payload['value'] = {'access_token': 'private-issued-token', 'expires_in': 0}
    result = env.client.post(BASE, json=body()).json()
    assert result['verified'] is False


def test_verification_expiry_and_display_text_are_honest(credentials_client):
    env = credentials_client
    response = env.client.post(BASE, json=body(name='<b>住宅</b>\n\x00', author='<script>owner</script>'))
    assert response.status_code == 200
    assert '<' not in response.json()['name'] and '\x00' not in response.json()['name']
    path = env.runtime._scope('t', 'p', 'wechat-oa') / 'credentials' / 'config.json'
    saved = json.loads(path.read_text())
    saved['verification_expires_at'] = '2020-01-01T00:00:00+00:00'
    path.write_text(json.dumps(saved))
    result = env.client.get(BASE).json()
    assert result['configured'] is True
    assert result['verified'] is False
    assert result['verified_at']
    assert not any(key in result for key in ['appSecret_cipher', 'appId_cipher', 'access_token'])


def test_clearing_api_credentials_preserves_browser_session(credentials_client):
    env = credentials_client
    assert env.client.post(BASE, json=body()).status_code == 200
    scope = env.runtime._scope('t', 'p', 'wechat-oa')
    (scope / 'status.json').write_text('{"state":"success"}')
    response = env.client.delete(BASE)
    assert response.status_code == 200
    assert response.json()['configured'] is False
    assert (scope / 'status.json').read_text() == '{"state":"success"}'
    assert not (scope / 'credentials' / 'config.json').exists()
    assert env.client.get(BASE).json()['configured'] is False


def test_oversized_credentials_payload_is_rejected_without_reflection(credentials_client):
    env = credentials_client
    response = env.client.post(BASE, json=body(name='x'*100000))
    assert response.status_code == 413
    assert SECRET not in response.text
    assert not env.records


def test_encoded_html_is_removed_from_public_credential_metadata(credentials_client):
    env = credentials_client
    response = env.client.post(BASE, json=body(name='&lt;b&gt;住宅&lt;/b&gt;', author='&lt;script&gt;owner&lt;/script&gt;'))
    assert response.status_code == 200
    assert response.json()['name'] == '住宅'
    assert response.json()['author'] == 'owner'
