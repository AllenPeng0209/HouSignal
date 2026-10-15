"""Project skills use real scoped SQLite records and deterministic reports."""
from dataclasses import replace
from datetime import date
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.business_models import Publication
from app.operations_models import ContentAccount, ContentPlan
from app.security import Context


@pytest.fixture
def project_case(tmp_path, monkeypatch):
    from app.config import settings
    import app.job_models  # Register all referenced tables before create_all.
    monkeypatch.setattr(settings, 'storage_root', tmp_path / 'storage')
    settings.storage_root.mkdir()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='其他组织'),
                User(id='u', email='editor@test.cn', password_hash='unused', name='编辑')])
    db.flush()
    db.add_all([Membership(user_id='u', tenant_id='t', role='content'),
                Property(id='p', tenant_id='t', name='江岸', city='苏州', channel='抖音'),
                Property(id='p2', tenant_id='t', name='私密外部项目', city='上海', channel='小红书')])
    db.flush()
    db.add_all([
        ContentAccount(id='account', tenant_id='t', property_id='p', platform='抖音', name='江岸实拍', positioning='真实空间', audience='改善家庭', voice='自然', red_lines='不许承诺升值'),
        ContentAccount(id='foreign-account', tenant_id='t', property_id='p2', platform='小红书', name='不可泄露账号'),
        ContentPlan(id='plan', tenant_id='t', property_id='p', title='本项目采光', channel='抖音', scheduled_for=date(2026, 10, 15), account_id='account', topic_id='light', status='scheduled'),
        ContentPlan(id='foreign-plan', tenant_id='t', property_id='p2', title='不可泄露排期', channel='小红书', topic_id='tour'),
        Publication(id='pub', tenant_id='t', property_id='p', release_id='release', title='本项目实拍', channel='抖音', url='https://www.douyin.com/video/123', platform_state='live', receipt_status='verified', metrics={'D7': {'views': 1000, 'likes': 100, 'comments': 10, 'saves': 5}, 'D30': None}),
        Publication(id='foreign-pub', tenant_id='t', property_id='p2', release_id='other-release', title='不可泄露作品', channel='小红书', url='https://example.org/private', platform_state='live', receipt_status='verified', metrics={'D7': {'views': 999999}}),
        Fact(id='fact', tenant_id='t', property_id='p', semantic_key='layout', label='户型', value='四室', source='户型图', status='verified'),
    ])
    db.commit()
    ctx = Context('u', 't', 'content', '编辑', 'editor@test.cn')
    yield db, ctx, tmp_path
    db.close()
    engine.dispose()


def module():
    try:
        available = importlib.util.find_spec('app.content_skills.project')
    except ModuleNotFoundError:
        available = None
    assert available is not None, 'native project skills module must exist'
    from app.content_skills import project
    return project


def run(case, skill, parameters=None, assets=None, text=''):
    db, ctx, tmp_path = case
    return module().execute(db, ctx, 'p', skill_name=skill, input_text=text,
                            parameters=parameters or {}, assets=assets or [], context={},
                            output_dir=tmp_path / skill)


def report(result):
    files = [Path(path) for path in result['artifact_paths']]
    assert files and all(path.is_file() for path in files)
    return json.loads(next(path for path in files if path.suffix == '.json').read_text())


def test_project_exports_do_not_leak_other_project_rows(project_case):
    for skill in ('skill-profile-manager', 'skill-content-calendar', 'skill-publish-log'):
        result = run(project_case, skill, {'action': 'export'})
        serialized = json.dumps(report(result), ensure_ascii=False) + result['response']
        assert '不可泄露' not in serialized
        assert 'foreign-' not in serialized


def test_inaccessible_project_is_rejected_before_artifact_write(project_case):
    db, ctx, tmp_path = project_case
    with pytest.raises(HTTPException) as caught:
        module().execute(db, replace(ctx, project_ids=['p2']), 'p', skill_name='asset-manager',
                         input_text='', parameters={}, assets=[], context={}, output_dir=tmp_path / 'denied')
    assert caught.value.status_code == 404
    assert not (tmp_path / 'denied').exists()


def test_publication_metrics_use_true_counts_and_preserve_unknown_windows(project_case):
    data = report(run(project_case, 'skill-publish-analytics'))
    assert data['summary']['views'] == 1000
    assert data['summary']['engagements'] == 115
    assert data['summary']['engagement_rate_pct'] == 11.5
    unknown = report(run(project_case, 'skill-social-performance-review', {'metric_window': 'D30'}))
    assert unknown['summary']['views'] is None
    assert unknown['summary']['metrics_available'] == 0


def test_roi_calculates_paid_campaign_and_missing_denominators(project_case):
    records = [{'name': '本项目投放', 'ad_spend': 100, 'production_cost': 50,
                'impressions': 1000, 'clicks': 100, 'engagements': 50,
                'conversions': 10, 'avg_order_value': 40}]
    data = report(run(project_case, 'roi-calculator', {'records': records}))
    metrics = data['ranking'][0]['metrics']
    assert metrics['ctr_pct'] == 10
    assert metrics['revenue'] == 400
    assert metrics['total_cost'] == 150
    assert metrics['profit'] == 250
    assert metrics['roi_pct'] == pytest.approx(166.66666666666666)
    missing = report(run(project_case, 'roi-calculator', {'records': [{'ad_spend': 100}]}))
    assert missing['ranking'][0]['metrics']['cpc'] is None
    assert missing['ranking'][0]['metrics']['roi_pct'] is None


def test_analytics_reads_selected_project_csv_and_rejects_foreign_asset(project_case):
    from app.config import settings
    db, _, _ = project_case
    payload = 'title,likes,comments\n第一条,5,10\n第二条,20,0\n'
    (settings.storage_root / 'metrics.csv').write_text(payload)
    for aid, pid in (('metrics', 'p'), ('foreign-metrics', 'p2')):
        db.add(Asset(id=aid, tenant_id='t', property_id=pid, name='数据.csv', kind='document', storage_key='metrics.csv', mime_type='text/csv', sha256='not-used', byte_size=len(payload), rights='authorized', rights_evidence='本项目导出'))
    db.commit()
    data = report(run(project_case, 'skill-post-scorer', {'asset_id': 'metrics', 'top_percent': 50}, [{'id': 'metrics', 'path': settings.storage_root / 'metrics.csv'}]))
    assert data['top_posts'][0]['title'] == '第一条'
    assert data['top_posts'][0]['engagement_score'] == 35
    assert data['summary']['top_threshold_score'] == 35
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-post-scorer', {'asset_id': 'foreign-metrics'}, [{'id': 'foreign-metrics', 'path': settings.storage_root / 'metrics.csv'}])
    assert caught.value.status_code == 404


def test_profile_and_calendar_write_through_existing_domain_constraints(project_case):
    db, _, _ = project_case
    created = report(run(project_case, 'skill-profile-manager', {'action': 'create', 'record': {
        'name': '新账号', 'platform': '小红书', 'positioning': '项目实景', 'audience': '改善家庭'}}))
    assert db.get(ContentAccount, created['record']['id']).property_id == 'p'
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-content-calendar-log', {'action': 'create', 'record': {
            'title': '错误平台', 'channel': '小红书', 'account_id': 'account', 'topic_id': 'tour'}})
    assert caught.value.status_code == 422
    candidate = report(run(project_case, 'skill-content-calendar-log', {'action': 'create', 'record': {
        'title': '候选排期', 'channel': '抖音', 'account_id': 'account', 'topic_id': 'tour', 'scheduled_for': '2026-10-16'}}))
    assert candidate['record']['status'] == 'draft'
    assert db.get(ContentPlan, candidate['record']['id']).status == 'draft'
    assert db.scalar(select(ContentAccount).where(ContentAccount.id == 'foreign-account')).name == '不可泄露账号'


def test_deterministic_comment_analysis_and_event_dates(project_case):
    data = report(run(project_case, 'skill-comment-insights', {'records': [
        {'content': '非常喜欢这个房子，想了解价格'}, {'content': '太差了，骗人'}, {'content': '位置在哪里？'}]}))
    assert data['total'] == 3
    assert data['sentiment']['positive']['count'] == 1
    assert data['sentiment']['negative']['count'] == 1
    assert data['sentiment']['neutral']['count'] == 1
    assert data['intents']['question']['count'] >= 1
    events = report(run(project_case, 'skill-event-calendar', {'year': 2025}))
    assert next(row['date'] for row in events['events'] if row['name'] == '中秋') == '2025-10-06'
    assert next(row['date'] for row in events['events'] if row['name'] == '春节') == '2025-01-29'


def test_short_link_preserves_query_and_notification_is_a_local_draft(project_case):
    linked = report(run(project_case, 'skill-short-link', {'url': 'https://example.org/project?id=7',
                       'source': 'douyin', 'medium': 'social', 'campaign': '江岸'}))
    assert 'id=7' in linked['tracked_url']
    assert 'utm_source=douyin' in linked['tracked_url']
    assert linked['shortened'] is False
    notification = report(run(project_case, 'skill-publish-notify', {'publication_id': 'pub'}))
    assert notification['status'] == 'draft'
    assert notification['sent'] is False
    assert '本项目实拍' in notification['message']
    with pytest.raises(HTTPException):
        run(project_case, 'skill-publish-notify', {'action': 'send', 'publication_id': 'pub'})


def test_invalid_numeric_input_and_arbitrary_file_paths_fail(project_case):
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'roi-calculator', {'records': [{'ad_spend': float('nan')} ]})
    assert caught.value.status_code == 422
    with pytest.raises(HTTPException):
        run(project_case, 'skill-post-scorer', {'path': '/etc/passwd'})


def test_follower_series_computes_real_change_without_imputing_missing_days(project_case):
    data = report(run(project_case, 'skill-data-tracker', {'records': [
        {'date': '2026-10-10', 'followers': 100, 'account_id': 'account'},
        {'date': '2026-10-11', 'followers': None, 'account_id': 'account'},
        {'date': '2026-10-13', 'followers': 120, 'account_id': 'account'}]}))
    assert data['follower_series'][1]['followers'] is None
    assert data['follower_changes'][0]['delta'] == 20
    assert data['follower_changes'][0]['growth_pct'] == 20
    assert data['follower_changes'][0]['elapsed_days'] == 3


def test_project_mutation_stays_in_transaction_and_reports_its_changed_id(project_case):
    db, _, _ = project_case
    result = run(project_case, 'skill-profile-manager', {'action': 'update',
                 'target_account_id': 'account', 'record': {'voice': '清楚而克制'}})
    assert result['mutation_refs'] == {'changed_account_id': 'account'}
    assert db.get(ContentAccount, 'account').voice == '清楚而克制'
    db.rollback()
    assert db.get(ContentAccount, 'account').voice == '自然'


def test_project_writer_obeys_role_and_foreign_record_constraints(project_case):
    db, ctx, tmp_path = project_case
    with pytest.raises(HTTPException) as caught:
        module().execute(db, replace(ctx, role='viewer'), 'p', skill_name='skill-profile-manager',
                         input_text='', parameters={'action': 'create', 'record': {'name': '越权', 'platform': '抖音'}},
                         assets=[], context={}, output_dir=tmp_path / 'denied-write')
    assert caught.value.status_code == 403
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-profile-manager', {'action': 'update', 'account_id': 'foreign-account', 'record': {'voice': '覆盖'}})
    assert caught.value.status_code == 404


def test_all_local_project_capabilities_create_real_distinct_outputs(project_case):
    selected_inputs = {
        'roi-calculator': {'records': [{'name': '投放', 'ad_spend': 10, 'impressions': 100}]},
        'skill-comment-insights': {'records': ['喜欢这个房子']},
        'skill-short-link': {'url': 'https://example.org/project'},
    }
    for name in module().PROJECT_SKILLS:
        result = run(project_case, name, selected_inputs.get(name))
        data = report(result)
        assert data['skill_name'] == name
        assert data['project_id'] == 'p'
        assert '江岸' in result['response']


def test_project_schema_supports_optional_input_and_json_parameter_controls(project_case):
    schema = module().definition('skill-profile-manager')['input_schema']
    assert schema['input_required'] is False
    assert schema['requires_model_authorization'] is False
    action = next(row for row in schema['parameters'] if row['key'] == 'action')
    assert action['type'] == 'select'
    assert 'create' in {row['value'] for row in action['options']}
    record = next(row for row in schema['parameters'] if row['key'] == 'record')
    assert (record['type'], record['format']) == ('text', 'json')


def test_postmortem_exposes_median_and_high_performer_evidence(project_case):
    data = report(run(project_case, 'skill-content-postmortem', {'records': [
        {'title': '普通作品', 'likes': 10, 'comments': 0},
        {'title': '第二条', 'likes': 10, 'comments': 0},
        {'title': '高互动作品', 'likes': 100, 'comments': 10}]}))
    assert data['cohort']['median_engagement_score'] == 10
    assert data['high_performers'][0]['title'] == '高互动作品'
    assert data['high_performers'][0]['engagement_score'] == 130


def test_aggregate_rate_uses_only_rows_with_both_observed_inputs(project_case):
    data = report(run(project_case, 'skill-publish-analytics', {'records': [
        {'title': '有播放量', 'views': 1000, 'engagements': 100},
        {'title': '播放量未观察', 'views': None, 'engagements': 50}]}))
    assert data['summary']['engagements'] == 150
    assert data['summary']['engagement_rate_pct'] == 10
    assert data['summary']['rate_sample_count'] == 1


@pytest.fixture
def outbound_providers(monkeypatch):
    project = module()
    configuration = SimpleNamespace(
        native_short_link_base_url='https://shortener.example.org/api',
        native_short_link_api_key='TEST_SHORTENER_KEY',
        native_short_link_allowed_hosts=['shortener.example.org'],
        native_notification_webhook_url='https://hooks.example.org/team/TEST_SECRET_TARGET',
        native_notification_key='TEST_NOTIFICATION_KEY',
        native_notification_allowed_hosts=['hooks.example.org'],
    )
    monkeypatch.setattr(project, 'settings', configuration, raising=False)
    state = {'requests': [], 'short_response': {'data': {'tiny_url': 'https://tinyurl.com/jiang-an'}},
             'notification_response': {'ok': True, 'message_id': 'message-7'},
             'status_code': 200}
    real_client = httpx.Client

    def respond(request):
        state['requests'].append(request)
        if state.get('error'):
            raise state['error']('PRIVATE_PROVIDER_ERROR token=TEST_SHORTENER_KEY /private/provider-secret')
        payload = state['short_response'] if request.url.host == 'shortener.example.org' else state['notification_response']
        return httpx.Response(state['status_code'], json=payload,
                              headers={'X-Request-ID': 'request-7'})

    monkeypatch.setattr(project, 'httpx', httpx, raising=False)
    monkeypatch.setattr(project.httpx, 'Client', lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)))
    if hasattr(project, 'socket'):
        monkeypatch.setattr(project.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('93.184.216.34', 443))])
    return configuration, state


def test_shortener_makes_real_adapter_request_and_returns_only_real_receipt(project_case, outbound_providers):
    _, state = outbound_providers
    result = run(project_case, 'skill-short-link', {'action': 'shorten',
                 'url': 'https://example.org/project?id=7', 'source': 'douyin'})
    data = report(result)
    assert data['shortened'] is True
    assert data['short_url'] == 'https://tinyurl.com/jiang-an'
    assert len(state['requests']) == 1
    request = state['requests'][0]
    assert (request.method, request.url.host, request.url.path) == ('POST', 'shortener.example.org', '/api/create')
    assert json.loads(request.content)['url'] == 'https://example.org/project?id=7&utm_source=douyin'
    assert request.headers['Authorization'] == 'Bearer TEST_SHORTENER_KEY'
    assert data['receipt']['http_status'] == 200
    public_output = json.dumps(data) + json.dumps(result['source_refs']) + result['response']
    assert 'TEST_SHORTENER_KEY' not in public_output
    assert 'TEST_SECRET_TARGET' not in public_output


def test_notify_requires_confirmation_and_sends_scoped_sanitized_message(project_case, outbound_providers):
    _, state = outbound_providers
    db, _, _ = project_case
    db.get(Publication, 'pub').title = '本项目实拍 token=PRIVATE_TOKEN 13800138000 /private/local-secret'
    db.flush()
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-publish-notify', {'action': 'send', 'publication_id': 'pub'})
    assert caught.value.status_code == 422
    assert not state['requests']
    result = run(project_case, 'skill-publish-notify', {'action': 'send', 'publication_id': 'pub', 'confirmed': True})
    data = report(result)
    assert data['status'] == 'sent'
    assert data['sent'] is True
    assert data['receipt']['message_id'] == 'message-7'
    assert len(state['requests']) == 1
    request = state['requests'][0]
    body = json.loads(request.content)
    assert isinstance(body['text'], str) and '本项目实拍' in body['text']
    assert 'PRIVATE_TOKEN' not in body['text']
    assert '13800138000' not in body['text']
    assert '/private/local-secret' not in body['text']
    assert '不可泄露' not in body['text']
    public_output = json.dumps(data) + json.dumps(result['source_refs']) + result['response']
    assert 'TEST_SECRET_TARGET' not in public_output
    assert 'TEST_NOTIFICATION_KEY' not in public_output
    assert 'PRIVATE_TOKEN' not in public_output


@pytest.mark.parametrize('skill, configuration_field, parameters', [
    ('skill-short-link', 'native_short_link_allowed_hosts', {'action': 'shorten', 'url': 'https://example.org/a'}),
    ('skill-publish-notify', 'native_notification_allowed_hosts', {'action': 'send', 'confirmed': True, 'publication_id': 'pub'}),
])
def test_unconfigured_or_unauthorized_provider_does_not_make_request(project_case, outbound_providers, skill, configuration_field, parameters):
    configuration, state = outbound_providers
    setattr(configuration, configuration_field, [])
    with pytest.raises(HTTPException) as caught:
        run(project_case, skill, parameters)
    assert caught.value.status_code == 409
    assert not state['requests']


@pytest.mark.parametrize('url', [
    'http://127.0.0.1/a', 'https://10.1.2.3/a', 'https://host.internal/a',
    'https://example.org/a?token=PRIVATE_TOKEN', 'https://example.org/a?phone=13800138000',
    'https://example.org/a?email=a%40example.org', 'https://user:password@example.org/a',
])
def test_shortener_rejects_private_or_access_bearing_links_without_request(project_case, outbound_providers, url):
    _, state = outbound_providers
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-short-link', {'action': 'shorten', 'url': url})
    assert caught.value.status_code == 422
    assert not state['requests']


@pytest.mark.parametrize('error', [ValueError, FileNotFoundError])
def test_provider_failures_never_surface_private_exception_text(project_case, outbound_providers, error):
    _, state = outbound_providers
    state['error'] = error
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-short-link', {'action': 'shorten', 'url': 'https://example.org/a'})
    assert caught.value.status_code == 503
    assert 'PRIVATE_PROVIDER_ERROR' not in caught.value.detail
    assert 'TEST_SHORTENER_KEY' not in caught.value.detail


def test_provider_error_receipts_and_unsafe_returned_short_url_are_rejected(project_case, outbound_providers):
    _, state = outbound_providers
    state['short_response'] = {'data': {'tiny_url': 'https://example.org/a?api_key=PRIVATE'}}
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-short-link', {'action': 'shorten', 'url': 'https://example.org/a'})
    assert caught.value.status_code == 503
    state['notification_response'] = {'errcode': 40001, 'errmsg': 'TEST_NOTIFICATION_KEY'}
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-publish-notify', {'action': 'send', 'confirmed': True, 'publication_id': 'pub'})
    assert caught.value.status_code == 503
    assert 'TEST_NOTIFICATION_KEY' not in caught.value.detail


def test_provider_private_dns_and_redirects_never_count_as_success(project_case, outbound_providers, monkeypatch):
    _, state = outbound_providers
    monkeypatch.setattr(module().socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('10.0.0.4', 443))])
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-short-link', {'action': 'shorten', 'url': 'https://example.org/a'})
    assert caught.value.status_code == 409
    assert not state['requests']
    monkeypatch.setattr(module().socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('93.184.216.34', 443))])
    state['status_code'] = 302
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-short-link', {'action': 'shorten', 'url': 'https://example.org/a'})
    assert caught.value.status_code == 503
    assert len(state['requests']) == 1


def test_notification_rechecks_current_membership_and_cancel_state(project_case, outbound_providers):
    _, state = outbound_providers
    db, _, _ = project_case
    member = db.scalar(select(Membership).where(Membership.user_id == 'u', Membership.tenant_id == 't'))
    member.role = 'viewer'
    db.flush()
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-publish-notify', {'action': 'send', 'confirmed': True, 'publication_id': 'pub'})
    assert caught.value.status_code == 403
    assert not state['requests']
    db.rollback()
    db.info['job_cancelled'] = lambda: True
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-publish-notify', {'action': 'send', 'confirmed': True, 'publication_id': 'pub'})
    assert caught.value.status_code == 409
    assert not state['requests']


def test_notification_foreign_record_is_rejected_without_http_request(project_case, outbound_providers):
    _, state = outbound_providers
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-publish-notify', {'action': 'send', 'confirmed': True, 'publication_id': 'foreign-pub'})
    assert caught.value.status_code == 404
    assert not state['requests']


@pytest.mark.parametrize('body', [{'ok': 'false'}, {'success': 'false'}, {'errcode': True}])
def test_notification_success_fields_require_valid_primitive_receipts(project_case, outbound_providers, body):
    _, state = outbound_providers
    state['notification_response'] = body
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-publish-notify', {'action': 'send', 'confirmed': True, 'publication_id': 'pub'})
    assert caught.value.status_code == 503


@pytest.mark.parametrize('skill', ['roi-calculator', 'skill-publish-analytics', 'skill-post-scorer'])
def test_empty_analytics_cannot_produce_a_successful_delivery(project_case, skill):
    with pytest.raises(HTTPException) as caught:
        run(project_case, skill, {'records': []})
    assert caught.value.status_code == 422


def test_unknown_metrics_are_explicitly_insufficient_for_delivery(project_case):
    data = report(run(project_case, 'skill-publish-analytics', {'metric_window': 'D30'}))
    assert data['data_quality']['status'] == 'insufficient_data'
    assert data['data_quality']['observed_records'] == 0
    assert data['summary']['views'] is None
    costs = report(run(project_case, 'roi-calculator', {'records': [{'ad_spend': 100}]}))
    assert costs['data_quality']['status'] == 'insufficient_data'
    assert costs['ranking'][0]['metrics']['roi_pct'] is None


def test_template_applies_real_structure_to_project_plan_and_rolls_back(project_case):
    db, _, _ = project_case
    result = run(project_case, 'template-library', {'action': 'apply', 'template_id': 'space-card',
                 'record': {'title': '验收空间卡片计划', 'channel': '抖音', 'account_id': 'account'}})
    data = report(result)
    plan = db.get(ContentPlan, data['record']['id'])
    assert plan.property_id == 'p'
    assert plan.status == 'draft'
    assert plan.topic_id == 'space'
    assert '一页一个空间观察' in plan.brief
    assert result['mutation_refs'] == {'changed_plan_id': plan.id}
    plan_id = plan.id
    db.rollback()
    assert db.get(ContentPlan, plan_id) is None


def test_template_apply_rejects_foreign_or_paused_account(project_case):
    db, _, _ = project_case
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'template-library', {'action': 'apply', 'template_id': 'space-card',
                      'record': {'title': '非法套用', 'channel': '小红书', 'account_id': 'foreign-account'}})
    assert caught.value.status_code == 404
    db.get(ContentAccount, 'account').status = 'paused'
    db.flush()
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'template-library', {'action': 'apply', 'template_id': 'space-card',
                      'record': {'title': '非法套用', 'channel': '抖音', 'account_id': 'account'}})
    assert caught.value.status_code == 409


def test_new_calendar_plan_cannot_escape_worker_transaction_rollback(project_case):
    db, _, _ = project_case
    result = run(project_case, 'skill-content-calendar-log', {'action': 'create', 'record': {
        'title': '验收可回滚候选排期', 'channel': '抖音', 'account_id': 'account',
        'topic_id': 'tour', 'scheduled_for': '2026-10-20'}})
    plan_id = result['changed_plan_id']
    assert db.get(ContentPlan, plan_id) is not None
    db.rollback()
    assert db.get(ContentPlan, plan_id) is None


def test_account_verification_blocks_before_browser_when_acceptance_project_has_no_session(project_case):
    db, ctx, tmp_path = project_case
    pid = 'isolated-native-acceptance-unlogged'
    db.add(Property(id=pid, tenant_id='t', name='只用于账号门槛验收', city='苏州', is_test=True))
    db.flush()
    with pytest.raises(HTTPException) as caught:
        module().execute(db, ctx, pid, skill_name='skill-my-account', input_text='',
                         parameters={'action': 'verify', 'platform': 'douyin'}, assets=[],
                         context={}, output_dir=tmp_path / 'no-account-identity')
    assert caught.value.status_code == 409
    assert not (tmp_path / 'no-account-identity').exists()


def test_empty_publish_log_is_not_a_commercial_release_record(project_case):
    db, _, _ = project_case
    db.delete(db.get(Publication, 'pub'))
    db.flush()
    with pytest.raises(HTTPException) as caught:
        run(project_case, 'skill-publish-log', {'action': 'export'})
    assert caught.value.status_code == 409


def test_profile_delivery_displays_written_brief_not_only_account_name(project_case):
    result = run(project_case, 'skill-profile-manager', {'action': 'update', 'account_id': 'account',
                 'record': {'voice': '用实拍空间和可核对事实作解释', 'audience': '独立改善家庭'}})
    assert '用实拍空间和可核对事实作解释' in result['response']
    assert '独立改善家庭' in result['response']
    assert '抖音' in result['response']


def test_calendar_delivery_displays_dates_channels_and_candidate_status(project_case):
    exported = run(project_case, 'skill-content-calendar', {'action': 'export'})
    assert '2026-10-15' in exported['response']
    assert '本项目采光' in exported['response']
    assert '抖音' in exported['response']
    created = run(project_case, 'skill-content-calendar-log', {'action': 'create', 'record': {
        'title': '验收明确候选日历', 'channel': '抖音', 'account_id': 'account',
        'topic_id': 'light', 'scheduled_for': '2026-10-17'}})
    assert '2026-10-17' in created['response']
    assert '候选草稿' in created['response']
