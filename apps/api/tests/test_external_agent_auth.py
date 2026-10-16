"""Public OAuth credentials preserve the live Web account/project boundaries."""
import base64
import hashlib
import importlib
import importlib.util
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core_models import LoginSession, Membership, Property, Tenant, User
from app.db import Base, get_db
from app.security import hash_password

ORIGIN = 'https://louxiang.example.test'
RESOURCE = ORIGIN + '/mcp'
REDIRECT = 'https://client.example.test/callback'
VERIFIER = 'v' * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()


@pytest.fixture
def env(monkeypatch):
    module = importlib.import_module('app.external_agent_auth') if importlib.util.find_spec('app.external_agent_auth') else None
    monkeypatch.setitem(settings.__dict__, 'external_agent_origin', ORIGIN)
    app = FastAPI()
    if module:
        from app.rate_limit import LoginLimiter
        for name in ('registration_limiter', 'authorization_limiter', 'token_limiter'):
            monkeypatch.setattr(module, name, LoginLimiter('test-limiter', limit=120))
        app.include_router(module.router)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t1', name='第一团队'), Tenant(id='t2', name='其他团队'),
                User(id='u1', email='owner@example.test', name='负责人', password_hash=hash_password('private'), active=True),
                User(id='u2', email='other@example.test', name='其他用户', password_hash=hash_password('private'), active=True)])
    db.flush()
    db.add_all([Membership(id='m1', user_id='u1', tenant_id='t1', role='admin', active=True),
                Membership(id='m2', user_id='u2', tenant_id='t2', role='admin', active=True),
                Property(id='p1', tenant_id='t1', name='陆家嘴', city='上海'),
                Property(id='p2', tenant_id='t1', name='栖岸', city='杭州'),
                Property(id='p3', tenant_id='t2', name='隔离项目', city='北京')])
    db.flush()
    for user, tenant in [('u1', 't1'), ('u2', 't2')]:
        token = 'browser-' + user
        db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user, tenant_id=tenant,
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.commit()
    def database():
        try:
            yield db
        except Exception:
            db.rollback()
            raise
    app.dependency_overrides[get_db] = database
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set('hs_session', 'browser-u1')
        yield client, db, module
    db.close()
    engine.dispose()


def register(client, redirect=REDIRECT):
    response = client.post('/api/agent/oauth/register', headers={'Origin': ORIGIN}, json={'client_name': '外部工作助手', 'redirect_uris': [redirect],
        'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']})
    assert response.status_code == 201, response.text
    assert 'client_secret' not in response.json()
    return response.json()['client_id']


def authorize(client, cid=None, **override):
    cid = cid or register(client)
    params = {'client_id': cid, 'redirect_uri': REDIRECT, 'response_type': 'code', 'resource': RESOURCE,
              'scope': 'louxiang:read louxiang:write offline_access', 'state': 'client-state',
              'code_challenge': CHALLENGE, 'code_challenge_method': 'S256'} | override
    response = client.get('/api/agent/oauth/authorize', params=params)
    return cid, response


def consent(client, page, **override):
    import re
    match = re.search(r'const authorizationRequest = ("[A-Za-z0-9_-]+");', page.text)
    assert match, page.text[:300]
    return client.post('/api/agent/oauth/consent', headers={'Origin': ORIGIN}, json={
        'request_id': json.loads(match[1]), 'project_ids': ['p1'],
        'scopes': ['louxiang:read', 'louxiang:write', 'offline_access']} | override)


def grant(client, scopes=None):
    cid, page = authorize(client, **({'scope': ' '.join(scopes)} if scopes else {}))
    assert page.status_code == 200, page.text
    response = consent(client, page, **({'scopes': scopes} if scopes else {}))
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()['redirect_url']).query)
    assert query['state'] == ['client-state']
    assert query['iss'] == [ORIGIN]
    return cid, query['code'][0]


def exchange(client, cid, code, **override):
    return client.post('/api/agent/oauth/token', headers={'Origin': ORIGIN}, data={'grant_type': 'authorization_code', 'client_id': cid,
        'code': code, 'redirect_uri': REDIRECT, 'resource': RESOURCE, 'code_verifier': VERIFIER} | override)


def credentials(client, scopes=None):
    cid, code = grant(client, scopes)
    response = exchange(client, cid, code)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return cid, response.json()


def test_discovery_is_canonical_not_host_derived(env):
    client, _, _ = env
    response = client.get('/.well-known/oauth-authorization-server', headers={'Host': 'attacker.invalid'})
    assert response.status_code == 200
    value = response.json()
    assert value['issuer'] == ORIGIN
    assert value['code_challenge_methods_supported'] == ['S256']
    assert value['authorization_response_iss_parameter_supported'] is True
    assert value['client_id_metadata_document_supported'] is True
    for path in ['/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp', '/mcp/.well-known/oauth-protected-resource']:
        assert client.get(path).json()['resource'] == RESOURCE


def test_pkce_consent_and_credentials_are_project_scoped_hashed(env):
    client, db, auth = env
    cid, payload = credentials(client)
    identity = auth.authenticate_agent(db, 'Bearer ' + payload['access_token'])
    assert identity.ctx.user_id == 'u1'
    assert identity.ctx.tenant_id == 't1'
    assert identity.project_ids == ('p1',)
    assert identity.ctx.project_ids == ['p1']
    assert auth.require_agent_project(db, identity, 'p1', ('louxiang:write',)).id == 'p1'
    for pid in ['p2', 'p3']:
        with pytest.raises(HTTPException) as error:
            auth.require_agent_project(db, identity, pid)
        assert error.value.status_code == 404
    from app.external_agent_models import AgentOAuthCredential
    rows = db.scalars(select(AgentOAuthCredential)).all()
    assert {r.token_hash for r in rows} == {hashlib.sha256(payload[k].encode()).hexdigest() for k in ['access_token', 'refresh_token']}
    assert all(payload['access_token'] not in str(r.__dict__) and payload['refresh_token'] not in str(r.__dict__) for r in rows)
    assert client.get('/api/agent/connections').json()[0]['project_ids'] == ['p1']
    assert payload['access_token'] not in client.get('/api/agent/connections').text


@pytest.mark.parametrize('override', [
    {'code_verifier': 'x' * 64}, {'resource': ORIGIN}, {'resource': 'https://attacker.invalid/mcp'},
    {'redirect_uri': REDIRECT + '?changed=1'}, {'client_id': 'missing'}])
def test_code_binding_rejects_pkce_resource_redirect_or_client_changes(env, override):
    client, _, _ = env
    cid, code = grant(client)
    assert exchange(client, cid, code, **override).status_code == 400
    assert exchange(client, cid, code).status_code == 200
    assert exchange(client, cid, code).status_code == 400


def test_refresh_rotation_replay_revokes_the_grant(env):
    client, db, auth = env
    cid, tokens = credentials(client)
    data = {'grant_type': 'refresh_token', 'client_id': cid, 'resource': RESOURCE, 'refresh_token': tokens['refresh_token']}
    refreshed = client.post('/api/agent/oauth/token', headers={'Origin': ORIGIN}, data=data)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['refresh_token'] != tokens['refresh_token']
    assert client.post('/api/agent/oauth/token', headers={'Origin': ORIGIN}, data=data).status_code == 400
    with pytest.raises(HTTPException):
        auth.authenticate_agent(db, 'Bearer ' + refreshed.json()['access_token'])


@pytest.mark.parametrize('change', ['revoked', 'expired', 'membership', 'user', 'project', 'role'])
def test_credentials_observe_live_revocation_membership_and_scope(env, change):
    client, db, auth = env
    _, tokens = credentials(client)
    from app.external_agent_models import AgentOAuthCredential, AgentOAuthGrant
    if change == 'revoked':
        db.scalar(select(AgentOAuthGrant)).revoked_at = datetime.now(timezone.utc)
    elif change == 'expired':
        db.scalar(select(AgentOAuthCredential).where(AgentOAuthCredential.kind == 'access')).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif change == 'membership':
        db.get(Membership, 'm1').active = False
    elif change == 'user':
        db.get(User, 'u1').active = False
    elif change == 'project':
        db.get(Membership, 'm1').project_ids = ['p2']
    else:
        db.get(Membership, 'm1').role = 'viewer'
    db.commit()
    if change == 'role':
        identity = auth.authenticate_agent(db, 'Bearer ' + tokens['access_token'])
        with pytest.raises(HTTPException) as error:
            auth.require_agent_project(db, identity, 'p1', ('louxiang:write',))
        assert error.value.status_code == 403
    elif change == 'project':
        identity = auth.authenticate_agent(db, 'Bearer ' + tokens['access_token'])
        assert identity.project_ids == ()
        with pytest.raises(HTTPException):
            auth.require_agent_project(db, identity, 'p1')
    else:
        with pytest.raises(HTTPException):
            auth.authenticate_agent(db, 'Bearer ' + tokens['access_token'])


def test_read_scope_is_independent_and_does_not_issue_refresh_without_offline_access(env):
    client, db, auth = env
    _, tokens = credentials(client, ['louxiang:read'])
    assert 'refresh_token' not in tokens
    identity = auth.authenticate_agent(db, 'Bearer ' + tokens['access_token'])
    assert auth.require_agent_project(db, identity, 'p1').id == 'p1'
    with pytest.raises(HTTPException) as error:
        auth.require_agent_project(db, identity, 'p1', ('louxiang:write',))
    assert error.value.status_code == 403


@pytest.mark.parametrize('redirect', ['http://client.example/cb', 'https://client.example/cb#fragment',
    'https://user@client.example/cb', 'javascript:alert(1)', 'http://127.0.0.2/cb', 'http://localhost.evil.test/cb'])
def test_registration_rejects_unsafe_redirects(env, redirect):
    client, _, _ = env
    assert client.post('/api/agent/oauth/register', headers={'Origin': ORIGIN}, json={'redirect_uris': [redirect]}).status_code == 400


def test_registration_accepts_rfc8252_loopback_dynamic_port_only(env):
    client, _, _ = env
    cid = register(client, 'http://127.0.0.1:3491/callback')
    _, page = authorize(client, cid, redirect_uri='http://127.0.0.1:59873/callback')
    assert page.status_code == 200
    assert authorize(client, cid, redirect_uri='http://127.0.0.1:59873/other')[1].status_code == 400
    assert authorize(client, cid, redirect_uri='http://localhost:59873/callback')[1].status_code == 400


@pytest.mark.parametrize('override', [{'project_ids': []}, {'project_ids': ['p2', 'p3']},
    {'scopes': ['louxiang:read', 'admin']}, {'scopes': ['louxiang:production']}, {'request_id': 'bad'}])
def test_consent_never_defaults_all_projects_or_increases_scope(env, override):
    client, db, _ = env
    _, page = authorize(client)
    assert consent(client, page, **override).status_code in {400, 403, 404}
    from app.external_agent_models import AgentOAuthGrant
    assert db.scalar(select(AgentOAuthGrant)) is None


def test_consent_requires_origin_session_cookie_and_one_use_binding(env):
    client, _, _ = env
    _, page = authorize(client)
    client.cookies.delete('lx_agent_auth')
    assert consent(client, page).status_code == 403
    _, page = authorize(client)
    assert consent(client, page).status_code == 200
    assert consent(client, page).status_code == 400
    _, page = authorize(client)
    import re
    rid = json.loads(re.search(r'const authorizationRequest = ("[A-Za-z0-9_-]+");', page.text)[1])
    data = {'request_id': rid, 'project_ids': ['p1'], 'scopes': ['louxiang:read']}
    assert client.post('/api/agent/oauth/consent', json=data).status_code == 403
    assert client.post('/api/agent/oauth/consent', headers={'Origin': 'https://attacker.invalid'}, json=data).status_code == 403
    client.cookies.delete('hs_session')
    assert client.post('/api/agent/oauth/consent', headers={'Origin': ORIGIN}, json=data).status_code == 401


def test_grant_management_is_current_user_and_organization_only(env):
    client, db, auth = env
    _, tokens = credentials(client)
    connection = client.get('/api/agent/connections').json()[0]
    client.cookies.set('hs_session', 'browser-u2')
    assert client.get('/api/agent/connections').json() == []
    assert client.delete('/api/agent/connections/' + connection['id'], headers={'Origin': ORIGIN}).status_code == 404
    client.cookies.set('hs_session', 'browser-u1')
    assert client.delete('/api/agent/connections/' + connection['id']).status_code == 403
    assert client.delete('/api/agent/connections/' + connection['id'], headers={'Origin': ORIGIN}).status_code == 200
    with pytest.raises(HTTPException):
        auth.authenticate_agent(db, 'Bearer ' + tokens['access_token'])


def test_arbitrary_cimd_urls_are_never_fetched(env, monkeypatch):
    client, _, auth = env
    def forbidden(*args, **kwargs):
        raise AssertionError('must not fetch untrusted client metadata')
    monkeypatch.setattr(auth, '_fetch_official_client_metadata', forbidden)
    for url in ['https://169.254.169.254/latest/meta-data', 'https://chatgpt.com.evil.test/oauth/client.json', 'https://chatgpt.com/oauth/unknown/client.json']:
        assert authorize(client, url)[1].status_code == 400


def test_official_cimd_metadata_uses_registered_redirect_and_public_client(env, monkeypatch):
    client, _, auth = env
    cid = 'https://chatgpt.com/oauth/client.json'
    monkeypatch.setattr(auth, '_fetch_official_client_metadata', lambda url: {
        'client_id': url, 'client_name': 'ChatGPT', 'redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect'],
        'token_endpoint_auth_methods_supported': ['none', 'private_key_jwt']})
    _, page = authorize(client, cid, redirect_uri='https://chatgpt.com/connector_platform_oauth_redirect')
    assert page.status_code == 200
    assert 'ChatGPT' in page.text
    assert authorize(client, cid, redirect_uri=REDIRECT)[1].status_code == 400


def test_duplicate_parameters_and_unsupported_scope_are_rejected_before_redirect(env):
    client, _, _ = env
    cid = register(client)
    assert authorize(client, cid, scope='admin')[1].status_code == 400
    assert authorize(client, cid, response_type='token')[1].status_code == 400
    assert authorize(client, cid, code_challenge_method='plain')[1].status_code == 400
    assert client.get('/api/agent/oauth/authorize?client_id=' + cid + '&client_id=evil').status_code == 400


def test_standard_oauth_error_payload_and_token_type_separation(env):
    client, db, auth = env
    response = client.post('/api/agent/oauth/token', headers={'Origin': ORIGIN}, data={'grant_type': 'password'})
    assert response.status_code == 400
    assert response.json()['error'] == 'invalid_target'
    _, tokens = credentials(client)
    for value in ['Bearer ' + tokens['refresh_token'], 'Bearer browser-u1', 'Basic ' + tokens['access_token'], 'Bearer ' + tokens['access_token'] + ' extra']:
        with pytest.raises(HTTPException):
            auth.authenticate_agent(db, value)


def test_whoami_exposes_only_current_scoped_identity_and_no_credentials(env):
    client, _, _ = env
    _, tokens = credentials(client)
    response = client.get('/api/agent/whoami', headers={'Authorization': 'Bearer ' + tokens['access_token']})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['user'] == {'id': 'u1', 'name': '负责人'}
    assert value['organization'] == {'id': 't1', 'name': '第一团队'}
    assert value['project_ids'] == ['p1']
    assert set(value['scopes']) == {'louxiang:read', 'louxiang:write', 'offline_access'}
    assert 'email' not in response.text
    assert tokens['access_token'] not in response.text
    assert client.get('/api/agent/whoami').status_code == 401


def test_identity_rechecks_permissions_changed_outside_sqlalchemy_identity_map(env):
    client, db, auth = env
    _, tokens = credentials(client)
    identity = auth.authenticate_agent(db, 'Bearer ' + tokens['access_token'])
    member = db.get(Membership, 'm1')
    from sqlalchemy import update
    db.execute(update(Membership).where(Membership.id == 'm1').values(role='viewer').execution_options(synchronize_session=False))
    assert member.role == 'admin'  # Existing ORM state is deliberately stale.
    with pytest.raises(HTTPException) as error:
        auth.require_agent_project(db, identity, 'p1', ('louxiang:write',))
    assert error.value.status_code == 403


def test_revoke_accepts_only_own_client_and_does_not_disclose_token_existence(env):
    client, db, auth = env
    cid, tokens = credentials(client)
    data = {'client_id': 'wrong-client', 'token': tokens['refresh_token']}
    assert client.post('/api/agent/oauth/revoke', headers={'Origin': ORIGIN}, data=data).json() == {}
    assert auth.authenticate_agent(db, 'Bearer ' + tokens['access_token']).grant_id
    data['client_id'] = cid
    assert client.post('/api/agent/oauth/revoke', headers={'Origin': ORIGIN}, data=data).json() == {}
    with pytest.raises(HTTPException):
        auth.authenticate_agent(db, 'Bearer ' + tokens['access_token'])


@pytest.mark.parametrize('kind', ['authorization', 'code', 'refresh'])
def test_expired_authorization_code_and_refresh_are_rejected(env, kind):
    client, db, _ = env
    from app.external_agent_models import AgentOAuthAuthorization, AgentOAuthCode, AgentOAuthCredential
    cid, page = authorize(client)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    if kind == 'authorization':
        db.scalar(select(AgentOAuthAuthorization)).expires_at = past
        db.commit()
        assert consent(client, page).status_code == 400
        return
    response = consent(client, page)
    code = parse_qs(urlsplit(response.json()['redirect_url']).query)['code'][0]
    if kind == 'code':
        db.scalar(select(AgentOAuthCode)).expires_at = past
        db.commit()
        assert exchange(client, cid, code).status_code == 400
        return
    tokens = exchange(client, cid, code).json()
    db.scalar(select(AgentOAuthCredential).where(AgentOAuthCredential.kind == 'refresh')).expires_at = past
    db.commit()
    assert client.post('/api/agent/oauth/token', headers={'Origin': ORIGIN}, data={'grant_type': 'refresh_token',
        'client_id': cid, 'resource': RESOURCE, 'refresh_token': tokens['refresh_token']}).status_code == 400


@pytest.mark.parametrize('role', ['owner', 'content'])
def test_existing_content_and_owner_roles_can_grant_workflow_permissions(env, role):
    client, db, auth = env
    db.get(Membership, 'm1').role = role
    db.commit()
    _, tokens = credentials(client, ['louxiang:read', 'louxiang:write', 'louxiang:production'])
    identity = auth.authenticate_agent(db, 'Bearer ' + tokens['access_token'])
    assert auth.require_agent_project(db, identity, 'p1', ('louxiang:production',)).id == 'p1'


@pytest.mark.parametrize('role', ['editor', 'reviewer', 'sales', 'viewer'])
def test_non_production_roles_cannot_grant_workflow_write_or_production(env, role):
    client, db, _ = env
    db.get(Membership, 'm1').role = role
    db.commit()
    _, page = authorize(client, scope='louxiang:read louxiang:write louxiang:production')
    assert consent(client, page, scopes=['louxiang:read', 'louxiang:write', 'louxiang:production']).status_code == 403


@pytest.mark.parametrize('payload', [
    {'grant_types': ['authorization_code', {'invalid': True}]}, {'grant_types': []},
    {'grant_types': [True]}, {'redirect_uris': ['https://client.example/cb', 42]},
    {'response_types': ['token']}, {'token_endpoint_auth_method': 'client_secret_post'}])
def test_invalid_registration_metadata_is_a_bounded_client_error(env, payload):
    client, _, _ = env
    data = {'redirect_uris': [REDIRECT]} | payload
    assert client.post('/api/agent/oauth/register', headers={'Origin': ORIGIN}, json=data).status_code == 400


def test_public_oauth_endpoints_accept_server_calls_without_browser_cookie(env):
    client, _, _ = env
    client.cookies.clear()
    response = client.post('/api/agent/oauth/register', json={'redirect_uris': [REDIRECT]})
    assert response.status_code == 201
    client.cookies.set('hs_session', 'browser-u1')
    assert client.post('/api/agent/oauth/register', json={'redirect_uris': [REDIRECT]}).status_code == 403


def test_registration_limit_is_bounded_and_does_not_persist_an_extra_client(env, monkeypatch):
    client, db, auth = env
    from app.rate_limit import LoginLimiter
    monkeypatch.setattr(auth, 'registration_limiter', LoginLimiter('oauth-test', limit=2), raising=False)
    for _ in range(2):
        assert client.post('/api/agent/oauth/register', headers={'Origin': ORIGIN}, json={'redirect_uris': [REDIRECT]}).status_code == 201
    response = client.post('/api/agent/oauth/register', headers={'Origin': ORIGIN}, json={'redirect_uris': [REDIRECT]})
    assert response.status_code == 429
    assert response.headers['retry-after'] == '60'
    from app.external_agent_models import AgentOAuthClient
    assert len(db.scalars(select(AgentOAuthClient)).all()) == 2


def test_official_codex_cimd_supports_only_documented_loopback_callbacks(env, monkeypatch):
    client, _, auth = env
    cid = 'https://chatgpt.com/oauth/codex/client.json'
    monkeypatch.setattr(auth, '_fetch_official_client_metadata', lambda url: {
        'client_id': url, 'client_name': 'Codex', 'application_type': 'native',
        'redirect_uris': ['http://127.0.0.1/callback', 'http://localhost/callback'],
        'token_endpoint_auth_method': 'none', 'token_endpoint_auth_methods_supported': ['none']})
    _, page = authorize(client, cid, redirect_uri='http://localhost:61783/callback')
    assert page.status_code == 200, page.text
    assert authorize(client, cid, redirect_uri='http://127.0.0.1:61783/different')[1].status_code == 400
    assert authorize(client, cid, redirect_uri='https://attacker.example/callback')[1].status_code == 400


def test_oauth_migration_roundtrip_matches_identity_models_without_business_changes():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    from sqlalchemy import inspect
    from app import external_agent_models
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/c24101500003_external_agents.py'
    spec = importlib.util.spec_from_file_location('external_oauth_migration_test', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    Tenant.__table__.create(engine)
    User.__table__.create(engine)
    tables = {cls.__table__.name: cls.__table__ for cls in [external_agent_models.AgentOAuthClient,
        external_agent_models.AgentOAuthAuthorization, external_agent_models.AgentOAuthGrant,
        external_agent_models.AgentOAuthCode, external_agent_models.AgentOAuthCredential]}
    with engine.begin() as conn:
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        actual = inspect(conn)
        assert set(actual.get_table_names()) == {'users', 'tenants'} | set(tables)
        for name, model in tables.items():
            assert {col['name'] for col in actual.get_columns(name)} == set(model.columns.keys())
        migration.downgrade()
        assert set(inspect(conn).get_table_names()) == {'users', 'tenants'}
    engine.dispose()


def test_disabled_cimd_advertises_dcr_and_keeps_pkce_issuer_flow_working(env, monkeypatch):
    client, _, auth = env
    monkeypatch.setitem(settings.__dict__, 'external_agent_cimd_enabled', False)
    metadata = client.get('/.well-known/oauth-authorization-server').json()
    assert metadata['client_id_metadata_document_supported'] is False
    assert metadata['registration_endpoint'] == ORIGIN + '/api/agent/oauth/register'
    assert metadata['code_challenge_methods_supported'] == ['S256']
    assert metadata['authorization_response_iss_parameter_supported'] is True
    _, tokens = credentials(client)
    assert tokens['resource'] == RESOURCE


@pytest.mark.parametrize('cid', ['https://chatgpt.com/oauth/client.json', 'https://chatgpt.com/oauth/codex/client.json'])
def test_disabled_cimd_never_fetches_or_accepts_url_clients(env, monkeypatch, cid):
    client, _, auth = env
    monkeypatch.setitem(settings.__dict__, 'external_agent_cimd_enabled', False)
    def forbidden(url):
        raise AssertionError('CIMD is disabled; no metadata fetch is permitted')
    monkeypatch.setattr(auth, '_fetch_official_client_metadata', forbidden)
    response = authorize(client, cid)[1]
    assert response.status_code == 400
    assert response.json()['error'] == 'invalid_client'
    assert '动态注册' in response.json()['error_description']


def test_cimd_is_enabled_by_default_when_optional_setting_is_absent(env, monkeypatch):
    client, _, _ = env
    monkeypatch.delitem(settings.__dict__, 'external_agent_cimd_enabled', raising=False)
    assert client.get('/.well-known/oauth-authorization-server').json()['client_id_metadata_document_supported'] is True
