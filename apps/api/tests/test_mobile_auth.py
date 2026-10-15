"""Native sessions share web tenancy rules without relying on browser cookies."""
import secrets
from datetime import datetime, timedelta, timezone
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core_models import LoginSession, Membership, Property, Tenant, User
from app.db import Base, get_db
from app.rate_limit import LoginLimiter
from app.security import hash_password
import app.main as main


PASSWORD = secrets.token_urlsafe(24)
TRUSTED_ORIGIN = main.settings.allowed_origins[0]


@pytest.fixture
def native_env(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t1', name='第一团队'), Tenant(id='t2', name='第二团队'),
                User(id='u1', email='owner@example.test', name='负责人', password_hash=hash_password(PASSWORD), active=True),
                User(id='u2', email='editor@example.test', name='编辑', password_hash=hash_password(PASSWORD), active=True)])
    db.flush()
    db.add_all([Membership(id='m1', user_id='u1', tenant_id='t1', role='admin', project_ids=None, active=True),
                Membership(id='m2', user_id='u2', tenant_id='t1', role='editor', project_ids=['p1'], active=True),
                Property(id='p1', tenant_id='t1', name='云境', city='上海'),
                Property(id='p2', tenant_id='t1', name='栖岸', city='杭州'),
                Property(id='p3', tenant_id='t2', name='外部项目', city='苏州')])
    db.commit()
    def database():
        try:
            yield db
        except Exception:
            db.rollback()
            raise
    main.app.dependency_overrides[get_db] = database
    monkeypatch.setattr(main, 'SessionLocal', sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(main, 'login_limiter', LoginLimiter('mobile-test-key'))
    with TestClient(main.app) as client:
        yield client, db
    main.app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def mobile_login(client, email='owner@example.test'):
    response = client.post('/api/mobile/auth/login', json={'email': email, 'password': PASSWORD})
    assert response.status_code == 200, response.text
    return response


def bearer(response):
    return {'Authorization': 'Bearer ' + response.json()['access_token']}


def test_native_login_returns_hashed_revocable_session_without_cookie(native_env):
    client, db = native_env
    response = mobile_login(client, ' OWNER@EXAMPLE.TEST ')
    payload = response.json()
    assert payload['token_type'] == 'bearer'
    assert payload['access_token'].startswith('hs_mobile_')
    assert datetime.fromisoformat(payload['expires_at']) > datetime.now(timezone.utc)
    assert payload['user'] == {'id': 'u1', 'name': '负责人', 'email': 'owner@example.test'}
    assert payload['organization'] == {'id': 't1', 'name': '第一团队'}
    assert payload['role'] == 'admin'
    assert 'set-cookie' not in response.headers
    assert not client.cookies
    assert response.headers['cache-control'] == 'no-store'
    session = db.scalar(select(LoginSession))
    assert session.token_hash != payload['access_token']
    assert session.token_hash == hashlib.sha256(payload['access_token'].encode()).hexdigest()
    assert client.get('/api/auth/me', headers=bearer(response)).json()['user']['id'] == 'u1'
    assert client.get('/api/auth/me').status_code == 401


def test_native_bearer_keeps_project_and_role_scope(native_env):
    client, _ = native_env
    headers = bearer(mobile_login(client, 'editor@example.test'))
    assert [project['id'] for project in client.get('/api/projects', headers=headers).json()] == ['p1']
    assert client.get('/api/projects/p2', headers=headers).status_code == 404
    assert client.get('/api/projects/p3', headers=headers).status_code == 404
    assert client.post('/api/projects', headers=headers, json={'name': '越权', 'city': '上海'}).status_code == 403
    response = client.patch('/api/projects/p1', headers=headers, json={'owner_name': '现场负责人'})
    assert response.status_code == 200
    assert client.get('/api/projects/p1', headers=headers).json()['owner_name'] == '现场负责人'


@pytest.mark.parametrize('change', ['membership_revoked', 'user_disabled', 'scope_restricted', 'role_downgraded'])
def test_native_session_checks_live_permissions_on_every_request(native_env, change):
    client, db = native_env
    headers = bearer(mobile_login(client))
    if change == 'membership_revoked':
        db.get(Membership, 'm1').active = False
    elif change == 'user_disabled':
        db.get(User, 'u1').active = False
    elif change == 'scope_restricted':
        db.get(Membership, 'm1').project_ids = ['p2']
    else:
        db.get(Membership, 'm1').role = 'viewer'
    db.commit()
    if change in {'membership_revoked', 'user_disabled'}:
        assert client.get('/api/projects', headers=headers).status_code == 403
    elif change == 'scope_restricted':
        assert client.get('/api/projects/p1', headers=headers).status_code == 404
        assert [row['id'] for row in client.get('/api/projects', headers=headers).json()] == ['p2']
    else:
        assert client.patch('/api/projects/p1', headers=headers, json={'name': '越权'}).status_code == 403


def test_native_logout_revokes_only_its_session(native_env):
    client, db = native_env
    first = bearer(mobile_login(client))
    second = bearer(mobile_login(client))
    assert client.post('/api/mobile/auth/logout', headers=first).status_code == 200
    assert client.get('/api/auth/me', headers=first).status_code == 401
    assert client.get('/api/auth/me', headers=second).status_code == 200
    assert len(db.scalars(select(LoginSession)).all()) == 1


@pytest.mark.parametrize('state', ['expired', 'revoked'])
def test_native_session_rejects_expired_or_deleted_token(native_env, state):
    client, db = native_env
    headers = bearer(mobile_login(client))
    session = db.scalar(select(LoginSession))
    if state == 'expired':
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    else:
        db.delete(session)
    db.commit()
    assert client.get('/api/auth/me', headers=headers).status_code == 401


@pytest.mark.parametrize('authorization', ['Basic not-a-token', 'Bearer', 'Bearer arbitrary', 'Bearer hs_mobile_bad', 'Bearer hs_mobile_' + 'a' * 64 + ' extra'])
def test_invalid_authorization_never_falls_back_to_cookie(native_env, authorization):
    client, _ = native_env
    web = client.post('/api/auth/login', json={'email': 'owner@example.test', 'password': PASSWORD})
    assert web.status_code == 200
    assert client.get('/api/auth/me', headers={'Authorization': authorization}).status_code == 401


def test_web_session_cannot_be_reused_as_native_bearer(native_env):
    client, _ = native_env
    client.post('/api/auth/login', json={'email': 'owner@example.test', 'password': PASSWORD})
    cookie = client.cookies.get('hs_session')
    client.cookies.clear()
    assert client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + cookie}).status_code == 401
    assert client.get('/api/auth/me', headers={'Authorization': 'Bearer hs_mobile_' + cookie}).status_code == 401


def test_native_token_cannot_be_reused_as_browser_cookie(native_env):
    client, _ = native_env
    response = mobile_login(client)
    client.cookies.set('hs_session', response.json()['access_token'])
    assert client.get('/api/auth/me').status_code == 401


def test_web_cookie_origin_protection_and_logout_are_preserved(native_env):
    client, _ = native_env
    response = client.post('/api/auth/login', json={'email': 'owner@example.test', 'password': PASSWORD})
    assert response.status_code == 200
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert client.get('/api/auth/me').status_code == 200
    assert client.patch('/api/projects/p1', json={'name': 'blocked'}).status_code == 403
    assert client.post('/api/auth/logout', headers={'Authorization': 'Bearer hs_mobile_' + 'a' * 64}).status_code == 403
    origin = {'Origin': TRUSTED_ORIGIN}
    assert client.patch('/api/projects/p1', headers=origin, json={'name': '更新名称'}).status_code == 200
    assert client.post('/api/auth/logout', headers=origin).status_code == 200
    assert client.get('/api/auth/me').status_code == 401


def test_mobile_logout_requires_bearer_and_does_not_revoke_web_cookie(native_env):
    client, _ = native_env
    client.post('/api/auth/login', json={'email': 'owner@example.test', 'password': PASSWORD})
    assert client.post('/api/mobile/auth/logout', headers={'Origin': TRUSTED_ORIGIN}).status_code == 401
    assert client.get('/api/auth/me').status_code == 200


def test_native_and_browser_login_share_rate_limit(native_env, monkeypatch):
    client, _ = native_env
    monkeypatch.setattr(main, 'login_limiter', LoginLimiter('mobile-test-key', limit=2))
    invalid = {'email': 'missing@example.test', 'password': PASSWORD}
    assert client.post('/api/auth/login', json=invalid).status_code == 401
    assert client.post('/api/mobile/auth/login', json=invalid).status_code == 401
    response = client.post('/api/mobile/auth/login', json=invalid)
    assert response.status_code == 429
    assert response.headers['retry-after'] == '60'
    assert client.post('/api/auth/login', json=invalid).status_code == 429


def test_native_login_rejects_oversized_body_before_credentials(native_env):
    client, _ = native_env
    response = client.post('/api/mobile/auth/login', content='a' * 8193, headers={'Content-Type': 'application/json'})
    assert response.status_code == 413


def test_native_requests_from_untrusted_browser_origin_are_rejected(native_env):
    client, _ = native_env
    headers = bearer(mobile_login(client)) | {'Origin': 'https://untrusted.example'}
    assert client.patch('/api/projects/p1', headers=headers, json={'name': 'blocked'}).status_code == 403


def test_authorization_and_request_id_are_allowed_by_cors(native_env):
    client, _ = native_env
    response = client.options('/api/projects', headers={
        'Origin': TRUSTED_ORIGIN, 'Access-Control-Request-Method': 'GET',
        'Access-Control-Request-Headers': 'authorization,x-request-id'})
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == TRUSTED_ORIGIN


@pytest.mark.parametrize('state,status', [('wrong_password', 401), ('disabled_user', 401), ('no_membership', 403)])
def test_native_login_rejects_unavailable_account_without_session(native_env, state, status):
    client, db = native_env
    password = PASSWORD
    if state == 'wrong_password':
        password = PASSWORD + secrets.token_hex(8)
    elif state == 'disabled_user':
        db.get(User, 'u1').active = False
    else:
        db.get(Membership, 'm1').active = False
    db.commit()
    response = client.post('/api/mobile/auth/login', json={'email': 'owner@example.test', 'password': password})
    assert response.status_code == status
    assert 'access_token' not in response.json()
    assert db.scalar(select(LoginSession)) is None


@pytest.mark.parametrize('role,project_ids', [('admin', ['p1']), ('editor', ['p1']), ('reviewer', ['p2']), ('admin', []), ('admin', None)])
def test_auth_response_exposes_current_membership_project_scope(native_env, role, project_ids):
    client, db = native_env
    membership = db.get(Membership, 'm1')
    membership.role = role
    membership.project_ids = project_ids
    db.commit()
    response = mobile_login(client)
    payload = response.json()
    scope_present = 'project_ids' in payload
    assert scope_present
    assert payload['project_ids'] == project_ids
    assert payload['role'] == role
    membership.project_ids = ['p2']
    db.commit()
    assert client.get('/api/auth/me', headers=bearer(response)).json()['project_ids'] == ['p2']
    web = client.post('/api/auth/login', json={'email': 'owner@example.test', 'password': PASSWORD})
    assert web.json()['project_ids'] == ['p2']
    assert client.get('/api/auth/me').json()['project_ids'] == ['p2']
