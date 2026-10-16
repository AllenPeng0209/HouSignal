"""External queue work retains the current OAuth and Web permission boundary."""
from datetime import datetime, timedelta, timezone
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.core_models import Fact, Membership, Property, Tenant, User
from app.db import Base, utcnow
from app.external_agent_models import AgentOAuthClient, AgentOAuthGrant
from app.job_models import Job
from app.worker import claim_job, execute_job, publish_progress


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'external_agent_origin', 'https://louxiang.example.test')
    engine = create_engine(f"sqlite:///{tmp_path / 'external-jobs.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([Tenant(id='t', name='研究组'), Tenant(id='other', name='其他组'),
                    User(id='u', email='worker@example.test', name='编辑', password_hash='x'),
                    User(id='other-u', email='other@example.test', name='其他编辑', password_hash='x')])
        db.flush()
        db.add_all([Membership(id='m', user_id='u', tenant_id='t', role='content', active=True),
                    Property(id='p', tenant_id='t', name='公开案例', city='上海'),
                    Property(id='p2', tenant_id='t', name='其他项目', city='上海'),
                    AgentOAuthClient(id='client', name='ChatGPT', redirect_uris=['https://chatgpt.com/callback']),
                    AgentOAuthClient(id='other-client', name='其他连接', redirect_uris=['https://other.test/callback'])])
        db.flush()
        db.add(AgentOAuthGrant(id='grant', organization_id='t', user_id='u', client_id='client',
            resource='https://louxiang.example.test/mcp', scope='louxiang:read louxiang:write louxiang:production',
            project_ids=['p'], expires_at=utcnow()+timedelta(days=30)))
        db.commit()
    yield factory
    engine.dispose()


def ticket(factory, *, external=True, kind='generate_content'):
    payload = {'external_agent': {'grant_id': 'grant', 'client_id': 'client',
        'scopes': ['louxiang:read', 'louxiang:write', 'louxiang:production'],
        'project_id': 'p', 'user_id': 'u'}} if external else {}
    with factory() as db:
        job = Job(id='job', tenant_id='t', property_id='p', user_id='u', kind=kind, payload=payload)
        db.add(job)
        db.commit()
        current = claim_job(db)
        return current.id, current.lease_token, current.attempt


def revoke(factory, field='revoked_at', value=None):
    with factory() as db:
        setattr(db.get(AgentOAuthGrant, 'grant'), field, value if value is not None else utcnow())
        db.commit()


def output(db, job):
    db.add(Fact(id='output', tenant_id=job.tenant_id, property_id=job.property_id,
                semantic_key='research', label='研究输出', value='待审核'))
    return {'fact_id': 'output'}


def test_revoked_grant_never_enters_handler(factory):
    claimed = ticket(factory)
    revoke(factory)
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': output})
    with factory() as db:
        assert db.get(Fact, 'output') is None
        job = db.get(Job, 'job')
        assert job.status == 'failed'
        assert job.result == {}
        assert '授权' in job.error


@pytest.mark.parametrize('kind', ['generate_content', 'render_content', 'content_skill'])
def test_valid_production_grant_commits_same_business_records(factory, kind):
    claimed = ticket(factory, kind=kind)
    assert execute_job(*claimed, session_factory=factory, handlers={kind: output})
    with factory() as db:
        assert db.get(Fact, 'output').value == '待审核'
        assert db.get(Job, 'job').status == 'succeeded'


def test_revocation_during_handler_rolls_back_business_output(factory):
    claimed = ticket(factory)
    def controlled(db, job):
        result = output(db, job)
        revoke(factory)
        return result
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': controlled})
    with factory() as db:
        assert db.get(Fact, 'output') is None
        assert db.get(Job, 'job').status == 'failed'
        assert db.get(Job, 'job').result == {}


def test_revocation_removes_only_the_handlers_private_generated_files(factory, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    generated = tmp_path / 'generated' / 'late-preview.mp4'
    source = tmp_path / 'source.txt'
    generated.parent.mkdir()
    source.write_text('已有用户素材')
    claimed = ticket(factory)
    def controlled(db, job):
        generated.write_bytes(b'controlled renderer output')
        db.info['created_private_files'] = [str(generated), str(source)]
        revoke(factory)
        return {'artifact': str(generated)}
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': controlled})
    assert not generated.exists()
    assert source.read_text() == '已有用户素材'
    with factory() as db:
        assert db.get(Job, 'job').result == {}
        assert str(tmp_path) not in db.get(Job, 'job').error


@pytest.mark.parametrize('change', [
    'expired', 'grant_user', 'grant_tenant', 'grant_client', 'grant_resource', 'grant_projects',
    'grant_scope', 'read_scope_removed', 'role', 'member_projects', 'inactive_user', 'inactive_member',
    'payload_user', 'payload_project', 'payload_client', 'missing_grant', 'malformed_marker',
])
def test_current_database_permissions_reject_mismatched_or_narrowed_grants(factory, change):
    claimed = ticket(factory)
    with factory() as db:
        grant, job = db.get(AgentOAuthGrant, 'grant'), db.get(Job, 'job')
        member = db.get(Membership, 'm')
        if change == 'expired': grant.expires_at = utcnow()-timedelta(seconds=1)
        elif change == 'grant_user': grant.user_id = 'other-u'
        elif change == 'grant_tenant': grant.organization_id = 'other'
        elif change == 'grant_client': grant.client_id = 'other-client'
        elif change == 'grant_resource': grant.resource = 'https://other.test/mcp'
        elif change == 'grant_projects': grant.project_ids = ['p2']
        elif change == 'grant_scope': grant.scope = 'louxiang:read louxiang:write'
        elif change == 'read_scope_removed': grant.scope = 'louxiang:production'
        elif change == 'role': member.role = 'editor'
        elif change == 'member_projects': member.project_ids = ['p2']
        elif change == 'inactive_user': db.get(User, 'u').active = False
        elif change == 'inactive_member': member.active = False
        elif change == 'payload_user': job.payload = {'external_agent': {**job.payload['external_agent'], 'user_id': 'other-u'}}
        elif change == 'payload_project': job.payload = {'external_agent': {**job.payload['external_agent'], 'project_id': 'p2'}}
        elif change == 'payload_client': job.payload = {'external_agent': {**job.payload['external_agent'], 'client_id': 'other-client'}}
        elif change == 'missing_grant': db.delete(grant)
        elif change == 'malformed_marker': job.payload = {'external_agent': None}
        db.commit()
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': output})
    with factory() as db:
        assert db.get(Fact, 'output') is None
        assert db.get(Job, 'job').status == 'failed'


def test_progress_is_not_published_after_grant_revocation(factory):
    claimed = ticket(factory)
    revoke(factory)
    with pytest.raises(HTTPException):
        publish_progress(*claimed, session_factory=factory, progress=.5, stage='制作中', preview_text='不应显示')
    with factory() as db:
        assert db.get(Job, 'job').result == {}
        assert db.get(Job, 'job').progress == .05


def test_provider_cancellation_poll_detects_grant_revocation_and_rolls_back(factory):
    claimed = ticket(factory)
    observed = []
    def controlled(db, job):
        output(db, job)
        revoke(factory)
        observed.append(db.info['job_cancelled']())
        return {'late': True}
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': controlled})
    assert observed == [True]
    with factory() as db:
        assert db.get(Fact, 'output') is None
        assert db.get(Job, 'job').status == 'failed'


def test_observed_revocation_never_retries_a_transient_provider_failure(factory):
    from app.provider import ProviderError
    claimed = ticket(factory)
    def controlled(db, job):
        revoke(factory)
        assert db.info['job_cancelled']()
        raise ProviderError('连接失败，请稍后重试')
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': controlled})
    with factory() as db:
        assert db.get(Job, 'job').status == 'failed'
        assert '授权' in db.get(Job, 'job').error


def test_heartbeat_detects_revocation_even_between_throttled_provider_polls(factory, monkeypatch):
    from app import worker
    class FastHeartbeatEvent(threading.Event):
        def wait(self, timeout=None):
            return super().wait(.005 if timeout == 20 else timeout)
    monkeypatch.setattr(worker, 'threading', SimpleNamespace(Event=FastHeartbeatEvent, Thread=threading.Thread))
    monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=lambda: 100.0))
    claimed = ticket(factory)
    observed = []
    def controlled(db, job):
        assert db.info['job_cancelled']() is False
        revoke(factory)
        # Poll reads are throttled for 200 ms. The independent heartbeat must
        # reject renewal and set the existing abort signal before that window.
        for _ in range(100):
            if db.info['job_cancelled']():
                observed.append(True)
                break
            time.sleep(.005)
        return {'late': True}
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': controlled})
    assert observed == [True]
    with factory() as db:
        assert db.get(Job, 'job').status == 'failed'


def test_thirty_day_grant_expires_at_its_recorded_boundary(factory, monkeypatch):
    from app import external_agent_job_guard as guard
    start = datetime(2026, 10, 15, tzinfo=timezone.utc)
    claimed = ticket(factory)
    with factory() as db:
        grant = db.get(AgentOAuthGrant, 'grant')
        grant.created_at = start
        grant.expires_at = start+timedelta(days=30)
        db.commit()
        job = db.get(Job, claimed[0])
        monkeypatch.setattr(guard, 'utcnow', lambda: start+timedelta(days=30, microseconds=-1))
        assert guard.external_job_access_allowed(db, job)
        monkeypatch.setattr(guard, 'utcnow', lambda: start+timedelta(days=30))
        assert not guard.external_job_access_allowed(db, job)


def test_plain_web_job_never_reads_oauth_tables_and_preserves_editor_role(factory):
    claimed = ticket(factory, external=False)
    with factory() as db:
        db.get(Membership, 'm').role = 'editor'
        db.commit()
    statements = []
    engine = factory.kw['bind']
    def observe(_connection, _cursor, sql, _parameters, _context, _many):
        statements.append(sql)
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        assert execute_job(*claimed, session_factory=factory, handlers={'generate_content': output})
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert not any('external_agent_' in sql for sql in statements)


def test_external_job_fails_closed_when_auth_schema_is_unavailable(factory):
    claimed = ticket(factory)
    with factory.kw['bind'].begin() as connection:
        connection.exec_driver_sql('DROP TABLE external_agent_grants')
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': output})
    with factory() as db:
        assert db.get(Fact, 'output') is None
        assert db.get(Job, 'job').status == 'failed'


def test_guard_refreshes_cached_identity_and_returns_project_restricted_context(factory):
    from app.external_agent_job_guard import check_external_job_access, external_job_access_allowed
    claimed = ticket(factory)
    with factory() as db:
        job = db.get(Job, claimed[0])
        ctx = check_external_job_access(db, job)
        assert ctx.project_ids == ['p']
        assert db.info['tenant_id'] == 't'
        assert db.info['user_id'] == 'u'
        revoke(factory)
        assert external_job_access_allowed(db, job) is False


def test_forged_payload_scope_neither_grants_permission_nor_overrides_real_grant(factory):
    from app.external_agent_job_guard import check_external_job_access
    claimed = ticket(factory)
    with factory() as db:
        job = db.get(Job, claimed[0])
        job.payload = {'external_agent': {**job.payload['external_agent'], 'scopes': []}}
        db.commit()
        assert check_external_job_access(db, job).role == 'content'
    revoke(factory, 'scope', 'louxiang:read')
    assert not execute_job(*claimed, session_factory=factory, handlers={'generate_content': output})
