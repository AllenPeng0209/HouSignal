"""Account skills combine scoped strategy records with cached platform evidence."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.core_models import Property, Tenant, User
from app.job_models import Job
from app.operations_models import ContentAccount
from app.security import Context


@pytest.fixture
def account_case(tmp_path, monkeypatch):
    from app import platform_accounts, platform_analytics, platform_credentials
    from app.platform_runtime import PLATFORMS
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([Tenant(id='t', name='团队'), User(id='u', name='编辑', email='editor@example.test', password_hash='unused')])
    db.flush()
    db.add_all([Property(id='p', tenant_id='t', name='江岸', city='苏州'),
                Property(id='p2', tenant_id='t', name='外部项目', city='上海')])
    db.flush()
    db.add_all([ContentAccount(id='brief', tenant_id='t', property_id='p', platform='抖音', name='手填定位名称'),
                ContentAccount(id='foreign-brief', tenant_id='t', property_id='p2', platform='抖音', name='不可泄露定位')])
    sid = 'a' * 64
    snapshot = {'platform': 'douyin', 'name': '抖音', 'account': {'name': '平台核验昵称', 'session_id': sid},
        'session_id': sid, 'status': 'partial', 'reason': '仅取得部分数据',
        'observed_at': '2026-10-14T00:20:00+00:00', 'source_observed_at': '2026-10-14T00:19:55+00:00',
        'metrics': {'followers': 330, 'following': None, 'likes': None, 'works': 12, 'reads': None},
        'period_metrics': [], 'items': [], 'source': 'easel_account_stats',
        'metric_scope': 'account_cumulative', 'metric_scopes': {'followers': 'account_cumulative'},
        'limitations': ['缺失指标不代表零'], 'cached': False}
    db.add_all([Job(id='current', tenant_id='t', property_id='p', user_id='u', kind='platform_analytics',
                   status='succeeded', payload={}, result={'platform_analytics': snapshot}),
                Job(id='foreign', tenant_id='t', property_id='p2', user_id='u', kind='platform_analytics',
                   status='succeeded', payload={}, result={'platform_analytics': {**snapshot,
                       'account': {'name': '不可泄露身份', 'session_id': sid}, 'metrics': {'followers': 999999}}})])
    db.commit()

    class CacheRuntime:
        session = sid
        unavailable = False

        def list_accounts(self, tenant, pid):
            assert (tenant, pid) == ('t', 'p'), 'cache must use caller project'
            if self.unavailable:
                raise OSError('cookie=private-value /Users/private')
            return [{'platform': pf, 'supported': True, 'ready': True,
                     'loggedIn': pf == 'douyin', 'verified': pf == 'douyin',
                     'identity_name': '平台核验昵称' if pf == 'douyin' else '', 'avatar': '',
                     'session_id': self.session if pf == 'douyin' else None} for pf in PLATFORMS]

        def login_status(self, tenant, pid, platform):
            assert (tenant, pid) == ('t', 'p'), 'analytics must use caller project'
            return {'loggedIn': platform == 'douyin', 'verified': True,
                    'session_id': self.session if platform == 'douyin' else None}

        def _scope(self, tenant, pid, platform, *, create=False):
            # Credentials metadata stays in this empty test directory.
            return tmp_path / tenant / pid / platform

        def whoami(self, *args, **kwargs):
            raise AssertionError('reading skill metadata must not launch a browser')

    runtime = CacheRuntime()
    for module in (platform_accounts, platform_analytics, platform_credentials):
        monkeypatch.setattr(module, 'runtime', runtime)
    yield SimpleNamespace(db=db, ctx=Context('u', 't', 'content', '编辑', 'editor@example.test'),
                          tmp_path=tmp_path, runtime=runtime)
    db.close()
    engine.dispose()


def execute(case, skill='skill-my-account', parameters=None, ctx=None):
    from app.content_skills import project
    output = case.tmp_path / skill
    result = project.execute(case.db, ctx or case.ctx, 'p', skill_name=skill, input_text='',
        parameters=parameters or {}, assets=[], context={}, output_dir=output)
    data = json.loads(next(Path(path) for path in result['artifact_paths'] if Path(path).suffix == '.json').read_text())
    return data, result


def test_my_account_reports_actual_session_separately_from_manual_strategy(account_case):
    data, result = execute(account_case)
    assert data['accounts'][0]['name'] == '手填定位名称'
    row = next(item for item in data['platform_accounts'] if item['platform'] == 'douyin')
    assert row['identity_name'] == '平台核验昵称' and row['loggedIn'] is True and row['verified'] is True
    snapshot = next(item for item in data['operational_snapshots'] if item['platform'] == 'douyin')
    assert snapshot['metrics']['followers'] == 330 and snapshot['metrics']['likes'] is None
    assert snapshot['status'] == 'partial' and snapshot['cached'] is True
    assert snapshot['observed_at'] == '2026-10-14T00:20:00+00:00'
    assert data['metadata_read_at'] != snapshot['observed_at']
    assert data['data_refreshed'] is False
    assert '不可泄露' not in json.dumps(data, ensure_ascii=False) + result['response']
    assert '平台核验昵称' in result['response'] and '330' in result['response']
    cache_ref = next(row for row in result['source_refs'] if row['type'] == 'platform_analytics_cache' and row['platform'] == 'douyin')
    assert cache_ref['project_id'] == 'p' and cache_ref['observed_at'] == '2026-10-14T00:20:00+00:00'


def test_account_filter_applies_to_strategy_sessions_and_cached_operational_data(account_case):
    data, _ = execute(account_case, parameters={'platform': '抖音', 'account_id': 'brief'})
    assert [row['id'] for row in data['accounts']] == ['brief']
    assert [row['platform'] for row in data['platform_accounts']] == ['douyin']
    assert [row['platform'] for row in data['operational_snapshots']] == ['douyin']
    assert data['strategy_identity_bound'] is False


def test_session_switch_hides_previous_account_metrics_in_my_account(account_case):
    account_case.runtime.session = 'b' * 64
    data, _ = execute(account_case)
    snapshot = next(row for row in data['operational_snapshots'] if row['platform'] == 'douyin')
    assert snapshot['status'] == 'session_changed'
    assert snapshot['metrics']['followers'] is None and snapshot['account'] is None


def test_tracker_uses_cached_current_account_snapshot_without_inventing_growth(account_case):
    data, result = execute(account_case, 'skill-data-tracker')
    assert data['follower_series'][0]['followers'] == 330
    assert data['follower_series'][0]['date'] == '2026-10-14'
    assert data['follower_changes'] == []
    assert data['summary']['views'] is None
    assert data['data_refreshed'] is False
    assert '平台核验昵称' in result['response']


def test_explicit_tracker_records_do_not_mix_in_platform_cache(account_case):
    account_case.runtime.unavailable = True
    data, _ = execute(account_case, 'skill-data-tracker', {'records': [
        {'date': '2026-10-10', 'account_id': 'brief', 'followers': 100},
        {'date': '2026-10-13', 'account_id': 'brief', 'followers': 120}]})
    assert data['follower_changes'][0]['delta'] == 20
    assert 'operational_snapshots' not in data


def test_metadata_failure_keeps_manual_account_and_safe_unknown_status(account_case):
    account_case.runtime.unavailable = True
    data, result = execute(account_case)
    assert data['accounts'][0]['name'] == '手填定位名称'
    assert data['platform_accounts_status'] == 'unavailable'
    assert 'private-value' not in json.dumps(data) + result['response']


def test_denied_project_cannot_read_account_cache_or_write_skill_report(account_case):
    with pytest.raises(HTTPException) as caught:
        execute(account_case, ctx=replace(account_case.ctx, project_ids=['p2']))
    assert caught.value.status_code == 404
    assert not (account_case.tmp_path / 'skill-my-account').exists()


def test_foreign_strategy_account_cannot_filter_actual_project_login(account_case):
    with pytest.raises(HTTPException) as caught:
        execute(account_case, parameters={'account_id': 'foreign-brief'})
    assert caught.value.status_code == 404


def test_invalid_platform_cannot_disguise_unknown_session_as_valid_empty_result(account_case):
    with pytest.raises(HTTPException) as caught:
        execute(account_case, parameters={'platform': '../../private'})
    assert caught.value.status_code == 422


def test_tracker_does_not_reuse_follower_point_after_account_switch(account_case):
    account_case.runtime.session = 'b' * 64
    data, _ = execute(account_case, 'skill-data-tracker')
    assert data['follower_series'] == [] and data['follower_changes'] == []
    assert next(row for row in data['operational_snapshots'] if row['platform'] == 'douyin')['status'] == 'session_changed'


def test_uncollected_platform_snapshot_preserves_null_metrics(account_case):
    data, _ = execute(account_case, parameters={'platform': 'xiaohongshu'})
    snapshot = data['operational_snapshots'][0]
    assert snapshot['status'] == 'not_refreshed' and snapshot['observed_at'] is None
    assert snapshot['metrics']['followers'] is None and snapshot['metrics']['likes'] is None
