"""Real queue/scope/result behavior; only the external platform boundary is faked."""
from contextlib import contextmanager
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

BASE = '/api/projects/p/operations/platform-analytics'


class PlatformBoundary:
    def __init__(self, root):
        self.root = root
        self.scripts = root / 'scripts'
        self.scripts.mkdir()
        for name in ('account_stats.py', 'bili_login.py', 'weixin_mp_stats.py'):
            (self.scripts / name).write_text('# adapter')
        self.python = '/known/python'
        self.session = '1111111111111111111111111111111111111111111111111111111111111111'
        self.logged_in = True
        self.verified = True
        self.locked = False
        self.busy = False

    def _scope(self, tenant, pid, platform, create=False):
        scope = self.root / tenant / pid / platform
        if create:
            scope.mkdir(parents=True, exist_ok=True)
        return scope

    def _env(self, scope):
        return {'EASEL_ROOT': str(scope)}

    def _private(self, scope):
        pass

    def login_status(self, tenant, pid, platform):
        return {'loggedIn': self.logged_in, 'verified': self.verified,
                'session_id': self.session, 'name': '住宅运营', 'cookie': 'private-cookie'}

    def whoami(self, tenant, pid, platform):
        assert self.locked, 'Identity probe must share the session lock'
        return self.login_status(tenant, pid, platform)

    @contextmanager
    def publish_lock(self, tenant, pid, platform):
        if self.busy:
            raise RuntimeError('private-cookie busy')
        self.locked = True
        try:
            yield
        finally:
            self.locked = False


@pytest.fixture
def analytics_env(tmp_path, monkeypatch):
    from app.db import Base, get_db
    from app.core_models import Tenant, User, Property, Membership
    from app.security import Context, get_context
    from app import jobs
    import app.business_models
    import app.agent_models
    import app.job_models
    import app.audit_models
    module = None
    if importlib.util.find_spec('app.platform_analytics'):
        from app import platform_analytics as module
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='另一团队'),
                User(id='u', email='content@test.cn', password_hash='unused', name='编辑', active=True)])
    db.flush()
    db.add(Membership(user_id='u', tenant_id='t', role='content', active=True))
    for pid, tenant in [('p', 't'), ('p2', 't'), ('foreign', 'other')]:
        db.add(Property(id=pid, tenant_id=tenant, name=pid, city='苏州', channel='抖音',
                        marketing_confirmed=True, marketing_evidence='书面授权'))
    db.commit()
    context = Context('u', 't', 'content', '编辑', 'content@test.cn', None)
    current = {'context': context}
    runtime = PlatformBoundary(tmp_path)
    if module:
        monkeypatch.setattr(module, 'runtime', runtime)
    monkeypatch.setattr(jobs, 'KINDS', jobs.KINDS | {'platform_analytics'})
    app = FastAPI()
    if module:
        app.include_router(module.router)
    def database():
        try:
            yield db
        except Exception:
            db.rollback()
            raise
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: current['context']
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, current=current, context=context,
                              module=module, runtime=runtime)
    db.close()
    engine.dispose()


def refresh(env, platform='douyin'):
    from app.job_models import Job
    response = env.client.post(BASE + f'/{platform}/refresh')
    assert response.status_code == 200, response.text
    return env.db.get(Job, response.json()['job_id'])


def install_reader(env, monkeypatch, payload, returncode=0, callback=None):
    def reader(db, spec):
        assert env.runtime.locked, 'Reader must not race login/logout/publishing'
        if callback:
            callback()
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload), stderr='private-cookie')
    monkeypatch.setattr(env.module, '_run_reader', reader)


def test_refresh_enqueues_scoped_bound_session_and_never_runs_external_work(analytics_env):
    env = analytics_env
    job = refresh(env)
    assert (job.kind, job.tenant_id, job.property_id, job.user_id) == ('platform_analytics', 't', 'p', 'u')
    assert job.payload == {'platform': 'douyin', 'session_id': '1111111111111111111111111111111111111111111111111111111111111111'}
    assert job.status == 'queued' and job.result == {}


def test_new_account_has_null_cached_metrics_and_no_implicit_zero_or_live_search(analytics_env):
    response = analytics_env.client.get(BASE)
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 7
    assert all(row['status'] == 'not_refreshed' and row['metrics']['followers'] is None for row in rows)
    assert 'private-cookie' not in response.text


@pytest.mark.parametrize('method,suffix', [('get', ''), ('post', '/douyin/refresh')])
def test_analytics_enforces_project_and_tenant_scope(analytics_env, method, suffix):
    env = analytics_env
    env.current['context'] = replace(env.context, project_ids=['p2'])
    assert getattr(env.client, method)(BASE + suffix).status_code == 404
    env.current['context'] = replace(env.context, tenant_id='other')
    assert getattr(env.client, method)(BASE + suffix).status_code == 404


def test_viewer_reads_cached_snapshots_but_cannot_enqueue(analytics_env):
    env = analytics_env
    env.current['context'] = replace(env.context, role='viewer')
    assert env.client.get(BASE).status_code == 200
    assert env.client.post(BASE + '/douyin/refresh').status_code == 403


def test_refresh_requires_real_connection_with_safe_session_binding(analytics_env):
    env = analytics_env
    env.runtime.logged_in = False
    assert env.client.post(BASE + '/douyin/refresh').status_code == 409
    env.runtime.logged_in = True
    env.runtime.session = '../../private-cookie'
    response = env.client.post(BASE + '/douyin/refresh')
    assert response.status_code == 409 and 'private-cookie' not in response.text


def test_worker_returns_actual_counts_and_only_allowlisted_public_work_data(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env)
    install_reader(env, monkeypatch, {
        'platform': 'douyin', 'loggedIn': True, 'nickname': '住宅运营', 'followers': 12,
        'likes': 0, 'posts': None, 'following': 4, 'cookie': 'private-cookie',
        'metrics': [{'label': '播放量', 'value': '1.2万', 'vs': '+3%'}],
        'metrics_title': '近7日', 'notes': [
            {'title': '公开住宅作品', 'url': 'https://www.douyin.com/video/123456789?token=private-cookie',
             'stat': '播放 2万', 'phone': '13800138000', 'private_message': 'secret'},
            {'title': '草稿', 'url': 'https://creator.douyin.com/creator-micro/content'},
            {'title': '外链', 'url': 'https://evil.test/video/42'},
        ], 'fetched_at': 1785000000})
    env.module.handle_platform_analytics(env.db, job)
    result = job.result['platform_analytics']
    assert result['status'] == 'partial' and result['metrics'] == {
        'followers': 12, 'following': 4, 'likes': 0, 'works': None, 'reads': None}
    assert result['account']['session_id'] == '1111111111111111111111111111111111111111111111111111111111111111'
    assert result['session_id'] == '1111111111111111111111111111111111111111111111111111111111111111'
    assert result['items'][0]['url'] == 'https://www.douyin.com/video/123456789'
    assert len(result['items']) == 1 and result['items'][0]['published_at'] is None
    assert 'private-cookie' not in json.dumps(job.result) and '13800138000' not in json.dumps(job.result)
    assert result['source'] == 'easel_account_stats' and result['observed_at']
    job.status = 'succeeded'
    env.db.commit()
    cached = next(row for row in env.client.get(BASE).json() if row['platform'] == 'douyin')
    assert cached['metrics']['followers'] == 12 and cached['cached'] is True


def test_account_swap_during_queue_never_reads_new_account(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env)
    env.runtime.session = '2222222222222222222222222222222222222222222222222222222222222222'
    install_reader(env, monkeypatch, {'loggedIn': True, 'followers': 99})
    env.module.handle_platform_analytics(env.db, job)
    result = job.result['platform_analytics']
    assert result['status'] == 'session_changed' and result['metrics']['followers'] is None


@pytest.mark.parametrize('verified,logged_in,expected', [(False, True, 'blocked'), (True, False, 'login_required')])
def test_uncertain_or_expired_identity_does_not_claim_data_ready(analytics_env, monkeypatch, verified, logged_in, expected):
    env = analytics_env
    job = refresh(env)
    env.runtime.verified, env.runtime.logged_in = verified, logged_in
    install_reader(env, monkeypatch, {'loggedIn': True, 'followers': 99})
    env.module.handle_platform_analytics(env.db, job)
    assert job.result['platform_analytics']['status'] == expected
    assert job.result['platform_analytics']['metrics']['followers'] is None


@pytest.mark.parametrize('payload,expected', [({'loggedIn': True, 'followers': None}, 'partial'),
    ({'loggedIn': True, 'followers': 12, 'error': 'token=private-cookie /Users/private'}, 'partial'),
    ({'loggedIn': False, 'followers': 12}, 'login_required')])
def test_empty_failed_or_logged_out_scrapes_remain_truthful(analytics_env, monkeypatch, payload, expected):
    env = analytics_env
    job = refresh(env)
    install_reader(env, monkeypatch, payload)
    env.module.handle_platform_analytics(env.db, job)
    result = job.result['platform_analytics']
    assert result['status'] == expected
    assert 'private-cookie' not in json.dumps(result)
    if expected == 'login_required':
        assert result['metrics']['followers'] is None


def test_cached_snapshot_not_reused_after_switching_platform_account(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env)
    install_reader(env, monkeypatch, {'loggedIn': True, 'followers': 12})
    env.module.handle_platform_analytics(env.db, job)
    job.status = 'succeeded'
    env.db.commit()
    env.runtime.session = '2222222222222222222222222222222222222222222222222222222222222222'
    row = next(row for row in env.client.get(BASE).json() if row['platform'] == 'douyin')
    assert row['status'] == 'session_changed' and row['metrics']['followers'] is None and row['account'] is None


def test_worker_rechecks_permission_after_external_read(analytics_env, monkeypatch):
    from app.core_models import Membership
    env = analytics_env
    job = refresh(env)
    def revoke():
        env.db.scalar(select(Membership).where(Membership.user_id == 'u')).active = False
        env.db.flush()
    install_reader(env, monkeypatch, {'loggedIn': True, 'followers': 12}, callback=revoke)
    with pytest.raises(HTTPException):
        env.module.handle_platform_analytics(env.db, job)
    assert job.result == {}


def test_busy_session_reports_busy_without_leaking_runner_error(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env)
    env.runtime.busy = True
    env.module.handle_platform_analytics(env.db, job)
    assert job.result['platform_analytics']['status'] == 'busy'
    assert 'private-cookie' not in json.dumps(job.result)
    job.status = 'succeeded'
    env.db.commit()
    row = next(row for row in env.client.get(BASE).json() if row['platform'] == 'douyin')
    assert row['status'] == 'busy'


def test_oa_list_totals_are_not_mislabeled_as_all_account_cumulative_totals(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env, 'wechat-oa')
    install_reader(env, monkeypatch, {'platform': 'wechat-oa', 'loggedIn': True,
        'followers': 12, 'likes': 5, 'reads': 99, 'posts': 21})
    env.module.handle_platform_analytics(env.db, job)
    result = job.result['platform_analytics']
    assert result['metric_scopes']['reads'] == 'returned_article_list'
    assert result['metric_scopes']['likes'] == 'returned_article_list'
    assert result['metric_scopes']['followers'] == 'account_cumulative'


def test_missing_core_fields_mark_partial_and_adapter_period_claim_is_unverified(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env)
    install_reader(env, monkeypatch, {'platform': 'douyin', 'loggedIn': True,
        'followers': None, 'following': 8, 'likes': 0, 'posts': None,
        'metrics_title': '平台近 7 日（创作中心只给这档）',
        'metrics': [{'label': '播放量', 'value': '211.56万', 'vs': ''}]})
    env.module.handle_platform_analytics(env.db, job)
    result = job.result['platform_analytics']
    assert result['status'] == 'partial'
    assert result['metrics']['followers'] is None and result['metrics']['likes'] == 0
    period = result['period_metrics'][0]
    assert period['value'] == '211.56万'
    assert period['source_label'] == '平台近 7 日（创作中心只给这档）'
    assert period['scope_verified'] is False and '待核对' in period['window']
    assert any('适配器周期标签' in line for line in result['limitations'])


def test_zero_core_counts_are_present_data_instead_of_missing(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env)
    install_reader(env, monkeypatch, {'platform': 'douyin', 'loggedIn': True,
        'followers': 0, 'posts': 0, 'likes': 0})
    env.module.handle_platform_analytics(env.db, job)
    result = job.result['platform_analytics']
    assert result['status'] == 'success' and result['metrics']['works'] == 0


def test_old_cached_claims_are_corrected_without_rewriting_historical_job(analytics_env):
    from app.job_models import Job
    env = analytics_env
    historical = {'platform_analytics': {
        'platform': 'douyin', 'name': '抖音', 'status': 'success', 'reason': '已读取账号数据',
        'account': {'session_id': env.runtime.session, 'name': '运营'},
        'session_id': env.runtime.session, 'observed_at': '2026-10-14T00:01:00+00:00',
        'metrics': {'followers': None, 'following': 8, 'likes': 0, 'works': None, 'reads': None},
        'period_metrics': [{'label': '播放量', 'value': '211.56万', 'comparison': None, 'window': '近7日'}],
        'items': [], 'limitations': [], 'cached': False}}
    job = Job(tenant_id='t', property_id='p', user_id='u', kind='platform_analytics',
              payload={'platform': 'douyin', 'session_id': env.runtime.session},
              result=historical, status='succeeded')
    env.db.add(job)
    env.db.commit()
    before = json.dumps(historical, ensure_ascii=False, sort_keys=True)
    # Select by platform rather than relying on platform-card ordering.
    row = next(item for item in env.client.get(BASE).json() if item['platform'] == 'douyin')
    assert row['status'] == 'partial'
    assert row['period_metrics'][0]['value'] == '211.56万'
    assert row['period_metrics'][0]['source_label'] == '近7日'
    assert row['period_metrics'][0]['scope_verified'] is False
    assert '待核对' in row['period_metrics'][0]['window']
    env.db.refresh(job)
    assert json.dumps(job.result, ensure_ascii=False, sort_keys=True) == before


def test_platform_mismatch_cannot_attribute_another_adapter_payload(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env)
    install_reader(env, monkeypatch, {'platform': 'bilibili', 'loggedIn': True, 'followers': 999})
    env.module.handle_platform_analytics(env.db, job)
    assert job.result['platform_analytics']['status'] == 'blocked'
    assert job.result['platform_analytics']['metrics']['followers'] is None


def test_cancelled_worker_does_not_write_a_result(analytics_env):
    from app.worker import LostLease
    env = analytics_env
    job = refresh(env)
    env.db.info['job_cancelled'] = lambda: True
    with pytest.raises(LostLease):
        env.module.handle_platform_analytics(env.db, job)
    assert job.result == {}


def test_reader_bounds_oversized_output_without_serializing_it(analytics_env, monkeypatch):
    env = analytics_env
    job = refresh(env)
    monkeypatch.setattr(env.module, '_run_reader', lambda db, spec: SimpleNamespace(
        returncode=0, stdout='private-cookie' * 100000, stderr=''))
    env.module.handle_platform_analytics(env.db, job)
    assert job.result['platform_analytics']['status'] == 'blocked'
    assert 'private-cookie' not in json.dumps(job.result)


def test_subprocess_boundary_drops_stderr_and_returns_only_bounded_stdout(analytics_env):
    env = analytics_env
    assert env.module is not None
    spec = {'command': [sys.executable, '-c',
        'import sys; print("{\\\"loggedIn\\\":true,\\\"followers\\\":5}"); print("private-cookie",file=sys.stderr)'],
        'cwd': str(env.runtime.root), 'env': {}}
    process = env.module._run_reader(env.db, spec)
    assert env.module._parse_output(process)['followers'] == 5
    assert process.stderr == '' and 'private-cookie' not in process.stdout


def test_subprocess_cancellation_terminates_owned_process_before_returning(analytics_env):
    from app.worker import LostLease
    env = analytics_env
    assert env.module is not None
    marker = env.runtime.root / 'owned-process.pid'
    spec = {'command': [sys.executable, '-c',
        'import os,pathlib,time; pathlib.Path(' + repr(str(marker)) + ').write_text(str(os.getpid())); time.sleep(15)'],
        'cwd': str(env.runtime.root), 'env': {}}
    env.db.info['job_cancelled'] = marker.exists
    started = time.monotonic()
    with pytest.raises(LostLease):
        env.module._run_reader(env.db, spec)
    assert time.monotonic() - started < 5
    pid = int(marker.read_text())
    import os
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_real_worker_keeps_safe_analytics_after_fenced_job_completion(tmp_path, monkeypatch):
    """A handler missing its return value would persist JSON null despite success."""
    from app import platform_analytics as module
    from app.db import Base
    from app.core_models import Tenant, User, Property, Membership
    from app.job_models import Job
    from app.security import context_for_user
    from app.worker import claim_job, execute_job
    engine = create_engine(f'sqlite:///{tmp_path / "analytics-worker.db"}',
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = PlatformBoundary(tmp_path)
    monkeypatch.setattr(module, 'runtime', runtime)
    def reader(db, spec):
        assert runtime.locked
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            'platform': 'douyin', 'loggedIn': True, 'followers': 8,
            'likes': 0, 'posts': 2, 'cookie': 'private-cookie'}), stderr='private-cookie')
    monkeypatch.setattr(module, '_run_reader', reader)
    try:
        with factory() as db:
            db.add_all([Tenant(id='t', name='合成团队'),
                        User(id='u', email='worker@test.cn', password_hash='unused', name='编辑', active=True)])
            db.flush()
            db.add(Membership(user_id='u', tenant_id='t', role='content', active=True))
            db.add(Property(id='p', tenant_id='t', name='合成住宅', city='苏州', channel='抖音', is_test=True))
            db.commit()
            queued = module.enqueue_platform_analytics(db, context_for_user(db, 'u', 't'), 'p', 'douyin')
            jid = queued['job_id']
            db.commit()
        with factory() as db:
            claimed = claim_job(db, kinds=('platform_analytics',))
            assert claimed.id == jid
            ticket = (jid, claimed.lease_token, claimed.attempt)
        assert execute_job(*ticket, session_factory=factory, handlers=module.registered_handlers)
        with factory() as db:
            completed = db.get(Job, jid)
            assert completed.status == 'succeeded'
            assert isinstance(completed.result, dict), 'Worker must persist handler return, not JSON null'
            result = completed.result['platform_analytics']
            assert result['status'] == 'success' and result['metrics']['followers'] == 8
            assert result['session_id'] == '1111111111111111111111111111111111111111111111111111111111111111'
            assert 'private-cookie' not in json.dumps(completed.result)
            rows = module.analytics_rows(db, context_for_user(db, 'u', 't'), 'p', 'douyin')
            assert rows[0]['metrics']['followers'] == 8
    finally:
        engine.dispose()


@pytest.mark.parametrize('platform,script,action,scope_flag', [
    ('douyin', 'account_stats.py', 'fetch', '--profile-base'),
    ('bilibili', 'bili_login.py', 'stats', '--cookie'),
    ('wechat-oa', 'weixin_mp_stats.py', 'stats', '--profile-base')])
def test_reader_command_is_fixed_read_only_and_uses_current_project(analytics_env, platform, script, action, scope_flag):
    env = analytics_env
    assert env.module is not None, 'Platform analytics execution boundary is missing'
    spec = env.module._reader_spec('t', 'p', platform)
    assert spec['command'][:3] == ['/known/python', str(env.runtime.scripts / script), action]
    target = str(env.runtime.root / 't' / 'p' / platform / ('cookies.json' if platform == 'bilibili' else 'profiles'))
    assert spec['command'][spec['command'].index(scope_flag) + 1] == target
    assert not any(arg in {'login', 'publish', 'upload', '--exec', '--dump-dir'} for arg in spec['command'])

