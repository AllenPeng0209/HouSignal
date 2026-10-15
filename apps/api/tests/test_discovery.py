"""Discovery calls real scoped queue paths; only external HTTP/browser reads are simulated."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.core_models import Membership, Property, Tenant, User
from app.db import Base, get_db
from app.job_models import Job
from app.security import Context, get_context
from app import jobs
import app.agent_models

ROOT = '/api/projects/p/operations/discovery'


@pytest.fixture
def discovery_client(tmp_path, monkeypatch):
    module = None
    if importlib.util.find_spec('app.discovery'):
        from app import discovery as module
    from app import douyin_discovery
    monkeypatch.setattr(douyin_discovery, 'search_public_douyin', lambda tenant_id, pid, request, cancelled=None:
        ([], {'platform': 'douyin', 'adapter': 'douyin_public_browser', 'status': 'not_connected',
              'reason': '当前项目尚未连接抖音研究账号'}))
    engine = create_engine(f'sqlite:///{tmp_path / "discovery.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='其他团队'),
                    User(id='u', email='editor@example.org', password_hash='unused', name='编辑')])
        db.flush()
        db.add_all([Membership(user_id='u', tenant_id='t', role='content'),
                    Property(id='p', tenant_id='t', name='住宅项目', city='苏州'),
                    Property(id='p2', tenant_id='t', name='其他项目', city='苏州')])
        db.commit()
    ctx = Context('u', 't', 'content', '编辑', 'editor@example.org', None)
    current = {'ctx': ctx}
    app = FastAPI()
    if module:
        app.include_router(module.router)
    app.include_router(jobs.router)
    def database():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: current['ctx']
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key-never-output')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setattr(settings, 'dashscope_model', 'qwen-plus')
    with TestClient(app) as client:
        yield client, factory, current, ctx
    engine.dispose()


def search(client, **overrides):
    return client.post(ROOT + '/search', json={'query': '住宅', 'platforms': ['bilibili'], 'days': 30,
        'min_likes': 1000, 'min_views': 100000, 'sort': 'engagement', **overrides})


def test_search_enqueues_owned_project_job_without_network(discovery_client):
    client, factory, _, _ = discovery_client
    response = search(client)
    assert response.status_code == 200, response.text
    with factory() as db:
        job = db.get(Job, response.json()['job_id'])
        assert (job.kind, job.status, job.user_id, job.property_id) == ('discovery_search', 'queued', 'u', 'p')
        assert job.payload['query'] == '住宅'


def test_search_enforces_tenant_project_and_content_role(discovery_client):
    client, _, current, ctx = discovery_client
    current['ctx'] = replace(ctx, role='viewer')
    assert search(client).status_code == 403
    current['ctx'] = replace(ctx, project_ids=['p2'])
    assert search(client).status_code == 404
    current['ctx'] = replace(ctx, tenant_id='other')
    assert search(client).status_code == 404


@pytest.mark.parametrize('extra', [{'query': ' '}, {'query': 'x' * 121}, {'days': 1}, {'min_likes': -1},
    {'platforms': ['weibo']}, {'platforms': []}, {'sort': 'invented'}, {'client_data': 'private'}])
def test_search_rejects_invalid_input(discovery_client, extra):
    client, _, _, _ = discovery_client
    assert search(client, **extra).status_code == 422


@pytest.fixture
def discovery_http(monkeypatch):
    from app import discovery
    real_client = httpx.Client
    now = datetime.now(timezone.utc)
    state = {'status': 200, 'sources': [
        {'url': 'https://www.bilibili.com/video/BV1Ab411c7XY/?spm_id_from=search', 'title': '住宅：苏州大平层实拍', 'index': 1, 'site_name': '哔哩哔哩'},
        {'url': 'https://www.bilibili.com/video/BV1Bb411c7XZ/', 'title': '豪宅户型实拍', 'index': 2, 'site_name': '哔哩哔哩'},
        {'url': 'https://www.douyin.com/video/7390000000000000001?token=private', 'title': '住宅空间改善', 'index': 3, 'site_name': '抖音'},
        {'url': 'https://www.xiaohongshu.com/explore/66abcdef1234567890123456?xsec_token=private', 'title': '住宅装修实拍', 'index': 4, 'site_name': '小红书'}],
        'count': 1, 'body': '模型伪造作品 https://www.bilibili.com/video/BV1Cb411c7XZ/ 点赞9999999',
        'posted': [], 'native_calls': [], 'native_status': 200, 'native_code': 0, 'native_sources': [
            {'bvid': 'BV1Ab411c7XY', 'arcurl': 'https://www.bilibili.com/video/BV1Ab411c7XY',
             'title': '苏州<em class=\"keyword\">住宅</em>大平层实拍', 'author': '住宅实拍作者',
             'pubdate': int((now-timedelta(days=3)).timestamp()), 'play': 200000, 'like': 4500, 'review': 100, 'favorites': 500},
            {'bvid': 'BV1Bb411c7XZ', 'arcurl': 'https://www.bilibili.com/video/BV1Bb411c7XZ',
             'title': '历史豪宅户型实拍', 'author': '历史作者',
             'pubdate': int((now-timedelta(days=180)).timestamp()), 'play': 1000000, 'like': 50000, 'review': 900, 'favorites': 8000}
        ], 'details': {
            'BV1Ab411c7XY': {'title': '苏州住宅大平层实拍', 'pubdate': int((now-timedelta(days=3)).timestamp()),
                'owner': {'name': '住宅实拍作者'}, 'stat': {'view': 200000, 'like': 4500, 'reply': 100, 'favorite': 500, 'share': 30}},
            'BV1Bb411c7XZ': {'title': '历史豪宅户型实拍', 'pubdate': int((now-timedelta(days=180)).timestamp()),
                'owner': {'name': '历史作者'}, 'stat': {'view': 1000000, 'like': 50000, 'reply': 900, 'favorite': 8000, 'share': 2000}},
        }}
    def boundary(request):
        if request.url.host == 'dashscope.aliyuncs.com':
            state['posted'].append(json.loads(request.content))
            output = {'choices': [{'message': {'role': 'assistant', 'content': state['body']}, 'finish_reason': 'stop'}]}
            if not state.get('missing_info'):
                output['search_info'] = {'search_results': state['sources']}
            return httpx.Response(state['status'], json={'output': output,
                'usage': {'input_tokens': 170, 'output_tokens': 50, 'plugins': {'search': {'count': state['count']}}},
                'request_id': 'public-mock-request'})
        if request.url.host == 'api.bilibili.com':
            if request.url.path == '/x/web-interface/search/type':
                state['native_calls'].append({'params': dict(request.url.params), 'headers': dict(request.headers)})
                return httpx.Response(state['native_status'], json={'code': state['native_code'], 'data': {'result': state['native_sources']}})
            if state.get('detail_timeout'):
                raise httpx.ReadTimeout('private provider diagnostic', request=request)
            details = state['details'].get(request.url.params.get('bvid'))
            return httpx.Response(200, json={'code': 0 if details else -404, 'data': details})
        raise AssertionError('Network attempted outside fixed provider and Bilibili hosts')
    monkeypatch.setattr(discovery, '_http_client', lambda: real_client(transport=httpx.MockTransport(boundary), follow_redirects=False))
    return state


def execute_search(client, factory, **overrides):
    from app import discovery
    from app.worker import claim_job, execute_job
    response = search(client, **overrides)
    assert response.status_code == 200, response.text
    jid = response.json()['job_id']
    with factory() as db:
        job = claim_job(db, kinds=('discovery_search',))
        ticket = (job.id, job.lease_token, job.attempt)
    success = execute_job(*ticket, session_factory=factory, handlers=discovery.registered_handlers)
    with factory() as db:
        row = db.get(Job, jid)
        return success, row


def test_real_sources_are_keyword_filtered_and_known_dates_apply_window(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    success, row = execute_search(client, factory, platforms=['douyin', 'xiaohongshu', 'bilibili'])
    assert success, row.error
    result = row.result['discovery_search']
    assert result['query'] == '住宅' and result['window_days'] == 30
    assert result['excluded_counts']['outside_window'] == 1
    assert len(result['items']) == result['total'] == 2
    verified = result['items'][0]
    assert verified['qualification'] == 'verified_high_engagement'
    assert verified['metrics'] == {'views': 200000, 'likes': 4500, 'comments': 100, 'saves': 500, 'shares': 30}
    assert verified['metrics_source'] == 'bilibili_public_api'
    assert verified['author'] == '住宅实拍作者' and verified['window_applied'] is True
    candidates = [item for item in result['items'] if item['qualification'] == 'candidate_unverified']
    assert len(candidates) == 1
    assert all(item['date_unknown'] and not item['window_applied'] for item in candidates)
    assert all(item['metrics'] == {'views': None, 'likes': None, 'comments': None, 'saves': None, 'shares': None} for item in candidates)
    assert all('?' not in item['url'] for item in result['items'])
    assert not any(item['content_id'] == 'BV1Cb411c7XZ' for item in result['items'])
    assert {source['platform']: source['status'] for source in result['sources']} == {
        'douyin': 'not_connected', 'xiaohongshu': 'partial', 'bilibili': 'partial'}
    posted = discovery_http['posted'][0]
    assert '住宅' in posted['input']['messages'][-1]['content']
    assert not any(word in json.dumps(posted, ensure_ascii=False) for word in ('住宅项目', 'editor@example.org', '苏州项目客户'))
    assert posted['parameters']['enable_search'] is True
    assert posted['parameters']['search_options'] == {'forced_search': True, 'enable_source': True,
        'search_strategy': 'turbo', 'assigned_site_list': ['xiaohongshu.com'], 'freshness': 30}
    from app.agent_models import ModelUsage
    with factory() as db:
        usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == row.id))
        assert (usage.input_tokens, usage.output_tokens) == (170, 50)


def test_every_search_requeries_and_all_time_marks_old_examples(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    first, _ = execute_search(client, factory, days=None, platforms=['douyin', 'xiaohongshu', 'bilibili'])
    second, row = execute_search(client, factory, days=None, platforms=['douyin', 'xiaohongshu', 'bilibili'])
    assert first and second
    assert len(discovery_http['posted']) == 2
    historical = next(item for item in row.result['discovery_search']['items'] if item['content_id'] == 'BV1Bb411c7XZ')
    assert historical['historical'] is True
    assert historical['published_at'] and historical['qualification'] == 'verified_high_engagement'
    assert 'freshness' not in discovery_http['posted'][0]['parameters']['search_options']


@pytest.mark.parametrize('missing_info,count', [(True, 0), (True, 1), (False, 0)])
def test_model_generated_urls_never_replace_search_evidence(discovery_client, discovery_http, missing_info, count):
    client, factory, _, _ = discovery_client
    discovery_http.update(missing_info=missing_info, count=count)
    success, row = execute_search(client, factory, platforms=['xiaohongshu'])
    assert not success
    assert '检索' in row.error
    assert row.result == {}


def test_empty_external_search_is_truthful_empty_not_error(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['sources'] = []
    success, row = execute_search(client, factory, platforms=['xiaohongshu'])
    assert success, row.error
    assert row.result['discovery_search']['items'] == []
    assert row.result['discovery_search']['total'] == 0


def test_bad_source_urls_duplicates_and_non_residential_results_are_discarded(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['sources'] += [
        {'url': 'https://bilibili.com/', 'title': '住宅主页'},
        {'url': 'https://bilibili.com.evil.test/video/BV1Db411c7XZ/', 'title': '住宅恶意域名'},
        {'url': 'https://user:private@www.bilibili.com/video/BV1Db411c7XZ/', 'title': '住宅凭证'},
        {'url': 'http://127.0.0.1/private', 'title': '住宅内网'},
        {'url': 'https://www.bilibili.com/video/BV1Ab411c7XY/?token=private', 'title': '住宅重复'},
        {'url': 'https://www.xiaohongshu.com/explore/66abcdef1234567890123457', 'title': '猫咪搞笑合集'},
    ]
    success, row = execute_search(client, factory, platforms=['douyin', 'xiaohongshu', 'bilibili'])
    assert success, row.error
    result = row.result['discovery_search']
    assert result['total'] == 2
    assert result['excluded_counts']['invalid_source'] == 4
    assert result['excluded_counts']['irrelevant'] == 1
    assert all('private' not in json.dumps(item) for item in result['items'])


def test_metric_failure_and_real_zero_are_distinct(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['details']['BV1Ab411c7XY']['stat'] = {'view': 0, 'like': 0, 'reply': 0, 'favorite': 0, 'share': 0}
    success, row = execute_search(client, factory)
    assert success
    item = row.result['discovery_search']['items'][0]
    assert item['metrics']['views'] == 0 and item['qualification'] == 'candidate_unverified'
    discovery_http['detail_timeout'] = True
    discovery_http['native_sources'][0].update(play=None, like=None, review=None, favorites=None)
    success, row = execute_search(client, factory)
    assert success
    result = row.result['discovery_search']
    assert result['sources'][0]['status'] == 'partial'
    assert all(item['metrics']['views'] is None for item in result['items'])
    assert all(item['qualification'] == 'candidate_unverified' for item in result['items'])


def test_expired_permissions_and_cancellation_stop_search(discovery_client, discovery_http):
    from app.worker import claim_job, execute_job
    from app import discovery
    client, factory, _, _ = discovery_client
    response = search(client)
    with factory() as db:
        job = claim_job(db, kinds=('discovery_search',))
        ticket = (job.id, job.lease_token, job.attempt)
        membership = db.scalar(select(Membership).where(Membership.user_id == 'u'))
        membership.project_ids = ['p2']
        db.commit()
    assert not execute_job(*ticket, session_factory=factory, handlers=discovery.registered_handlers)
    assert discovery_http['posted'] == []
    with factory() as db:
        membership = db.scalar(select(Membership).where(Membership.user_id == 'u'))
        membership.project_ids = None
        db.commit()
    response = search(client)
    assert client.post('/api/jobs/' + response.json()['job_id'] + '/cancel').status_code == 200
    with factory() as db:
        assert claim_job(db, kinds=('discovery_search',)) is None
    assert discovery_http['posted'] == []


@pytest.mark.parametrize('url', ['http://127.0.0.1/private', 'https://bilibili.com.evil.test/video/BV1Ab411c7XY',
    'https://user:password@www.bilibili.com/video/BV1Ab411c7XY', 'https://www.bilibili.com/',
    'https://www.douyin.com/video/7390000000000000001', 'https://www.bilibili.com/video/BV1Ab411c7XY/?token=secret'])
def test_inspect_rejects_non_public_work_links(discovery_client, url):
    client, _, _, _ = discovery_client
    assert client.post(ROOT + '/inspect', json={'url': url}).status_code == 422


def test_inspect_reads_actual_public_metrics_from_fixed_host(discovery_client, discovery_http):
    client, _, current, ctx = discovery_client
    response = client.post(ROOT + '/inspect', json={'url': 'https://www.bilibili.com/video/BV1Ab411c7XY/'})
    assert response.status_code == 200, response.text
    item = response.json()['item']
    assert item['metrics']['views'] == 200000
    assert item['title'] == '苏州住宅大平层实拍'
    assert item['published_at'] and item['metrics_observed_at']
    current['ctx'] = replace(ctx, role='viewer')
    assert client.post(ROOT + '/inspect', json={'url': item['url']}).status_code == 403


def test_specific_city_and_residential_query_do_not_return_other_cities(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['sources'].append({'url': 'https://www.xiaohongshu.com/explore/66abcdef1234567890123458', 'title': '杭州大平层住宅实拍'})
    success, row = execute_search(client, factory, query='苏州 大平层', platforms=['xiaohongshu', 'bilibili'], days=None)
    assert success, row.error
    assert [item['title'] for item in row.result['discovery_search']['items']] == ['苏州住宅大平层实拍']
    assert '苏州 大平层' in discovery_http['posted'][0]['input']['messages'][-1]['content']


def test_paid_failed_source_validation_keeps_actual_usage_receipt(discovery_client, discovery_http):
    from app.agent_models import ModelUsage
    client, factory, _, _ = discovery_client
    discovery_http.update(missing_info=True)
    success, row = execute_search(client, factory, platforms=['xiaohongshu'])
    assert not success
    with factory() as db:
        usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == row.id))
        assert usage is not None
        assert (usage.input_tokens, usage.output_tokens) == (170, 50)


def test_daily_budget_blocks_provider_and_does_not_drop_previous_usage(discovery_client, discovery_http, monkeypatch):
    from app.agent_models import ModelUsage
    client, factory, _, _ = discovery_client
    monkeypatch.setattr(settings, 'development_token_budget', 100000)
    with factory() as db:
        db.add(ModelUsage(tenant_id='t', property_id='p', job_id='prior', model='qwen-plus', input_tokens=100000, output_tokens=0))
        db.commit()
    success, row = execute_search(client, factory, platforms=['xiaohongshu'])
    assert not success and '用量' in row.error
    assert discovery_http['posted'] == []
    with factory() as db:
        assert db.scalar(select(ModelUsage).where(ModelUsage.job_id == 'prior')).input_tokens == 100000


def test_disabled_and_untrusted_provider_never_send_network(discovery_client, discovery_http, monkeypatch):
    client, factory, _, _ = discovery_client
    monkeypatch.setattr(settings, 'dashscope_base_url', 'http://127.0.0.1/private')
    success, row = execute_search(client, factory, platforms=['xiaohongshu'])
    assert not success and '地址配置' in row.error
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    success, row = execute_search(client, factory, platforms=['xiaohongshu'])
    assert not success and '配置' in row.error
    assert discovery_http['posted'] == []


def test_residential_natural_query_preserves_related_housing_aliases(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    success, row = execute_search(client, factory, query='住宅相关的爆款', days=None)
    assert success, row.error
    assert len(row.result['discovery_search']['items']) == 2
    assert '历史豪宅户型实拍' in [item['title'] for item in row.result['discovery_search']['items']]


def test_index_claims_of_likes_and_dates_are_not_accepted_as_platform_facts(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['sources'][3].update(likes=9000000, views=9000000, published_at=datetime.now(timezone.utc).isoformat())
    success, row = execute_search(client, factory, platforms=['xiaohongshu'])
    assert success, row.error
    item = row.result['discovery_search']['items'][0]
    assert item['metrics']['likes'] is None and item['published_at'] is None
    assert item['qualification'] == 'candidate_unverified'


def test_cancellation_during_external_search_cannot_publish_results(discovery_client, discovery_http):
    from app.worker import claim_job, execute_job
    from app import discovery
    from app.agent_models import ModelUsage
    client, factory, _, _ = discovery_client
    response = search(client, platforms=['xiaohongshu'])
    with factory() as db:
        row = claim_job(db, kinds=('discovery_search',))
        ticket = row.id, row.lease_token, row.attempt
    original = discovery._http_client
    class CancellingClient:
        def __enter__(self):
            self.client = original().__enter__()
            return self
        def __exit__(self, *args):
            self.client.close()
        def post(self, *args, **kwargs):
            result = self.client.post(*args, **kwargs)
            client.post('/api/jobs/' + ticket[0] + '/cancel')
            return result
    from unittest.mock import patch
    with patch.object(discovery, '_http_client', CancellingClient):
        assert not execute_job(*ticket, session_factory=factory, handlers=discovery.registered_handlers)
    with factory() as db:
        row = db.get(Job, ticket[0])
        assert row.status == 'cancelled' and row.result == {}
        usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == row.id))
        assert usage.input_tokens == 170


def test_zero_matching_works_reports_partial_not_metrics_success(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['native_sources'] = [
        {'url': 'https://space.bilibili.com/12345/lists?sid=42', 'title': '住宅作者账号'},
        {'url': 'https://www.bilibili.com/read/cv123456', 'title': '住宅资讯'},
    ]
    success, row = execute_search(client, factory)
    assert success, row.error
    result = row.result['discovery_search']
    assert result['items'] == [] and result['excluded_counts']['invalid_source'] == 2
    assert result['sources'][0]['status'] == 'partial'
    assert '仅本次有限候选' in result['sources'][0]['reason']


def test_latest_orders_by_verified_publication_date_before_engagement(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['native_sources'].append({'bvid': 'BV1Db411c7XW', 'arcurl': 'https://www.bilibili.com/video/BV1Db411c7XW/',
        'title': '住宅最新实拍', 'pubdate': int((datetime.now(timezone.utc)-timedelta(days=1)).timestamp()), 'play': 300, 'like': 10})
    discovery_http['details']['BV1Db411c7XW'] = {
        'title': '住宅最新实拍', 'pubdate': int((datetime.now(timezone.utc)-timedelta(days=1)).timestamp()),
        'owner': {'name': '新作品作者'}, 'stat': {'view': 300, 'like': 10, 'reply': 0, 'favorite': 1, 'share': 0}}
    success, row = execute_search(client, factory, sort='latest')
    assert success, row.error
    items = row.result['discovery_search']['items']
    assert [item['content_id'] for item in items] == ['BV1Db411c7XW', 'BV1Ab411c7XY']
    assert items[0]['qualification'] == 'candidate_unverified'
    assert items[1]['qualification'] == 'verified_high_engagement'


def test_bilibili_native_search_uses_keywords_sort_and_no_model_credentials(discovery_client, discovery_http, monkeypatch):
    client, factory, _, _ = discovery_client
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    success, row = execute_search(client, factory, query='苏州 大平层', sort='latest', days=None)
    assert success, row.error
    assert discovery_http['posted'] == []
    assert discovery_http['native_calls'][0]['params'] == {
        'search_type': 'video', 'keyword': '苏州 大平层', 'page': '1', 'page_size': '20', 'order': 'pubdate'}
    headers = discovery_http['native_calls'][0]['headers']
    assert headers['user-agent'] == 'Mozilla/5.0' and headers['referer'] == 'https://search.bilibili.com/'
    assert 'cookie' not in headers and 'authorization' not in headers
    result = row.result['discovery_search']
    assert result['sources'][0]['adapter'] == 'bilibili_public_search+bilibili_public_api'
    assert result['sources'][0]['status'] == 'ok'
    assert [item['title'] for item in result['items']] == ['苏州住宅大平层实拍']


@pytest.mark.parametrize(('sort', 'order'), [('engagement', 'click'), ('relevance', 'totalrank')])
def test_native_search_obeys_platform_order(discovery_client, discovery_http, sort, order):
    client, factory, _, _ = discovery_client
    success, row = execute_search(client, factory, sort=sort, days=None)
    assert success, row.error
    assert discovery_http['native_calls'][0]['params']['order'] == order


def test_native_search_metrics_survive_detail_failure_without_guessing_shares(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['detail_timeout'] = True
    success, row = execute_search(client, factory)
    assert success, row.error
    result = row.result['discovery_search']
    assert result['excluded_counts']['outside_window'] == 1
    assert result['total'] == 1
    item = result['items'][0]
    assert item['metrics'] == {'views': 200000, 'likes': 4500, 'comments': 100, 'saves': 500, 'shares': None}
    assert item['metrics_source'] == 'bilibili_public_search'
    assert item['published_at'] and item['qualification'] == 'verified_high_engagement'
    assert item['title'] == '苏州住宅大平层实拍'
    assert item['raw_metrics']['play'] == 200000
    assert result['sources'][0]['status'] == 'partial'


@pytest.mark.parametrize('status', [403, 412])
def test_native_block_keeps_selected_other_platform_search_running(discovery_client, discovery_http, status):
    client, factory, _, _ = discovery_client
    discovery_http['native_status'] = status
    success, row = execute_search(client, factory, platforms=['bilibili', 'douyin', 'xiaohongshu'])
    assert success, row.error
    result = row.result['discovery_search']
    sources = {source['platform']: source for source in result['sources']}
    assert sources['bilibili']['status'] == 'blocked'
    assert result['total'] == 1
    assert {item['platform'] for item in result['items']} == {'xiaohongshu'}
    assert sources['douyin']['status'] == 'not_connected'
    assert discovery_http['posted'][0]['parameters']['search_options']['assigned_site_list'] == ['xiaohongshu.com']
    success, row = execute_search(client, factory)
    assert success and row.result['discovery_search']['total'] == 0
    assert row.result['discovery_search']['sources'][0]['status'] == 'blocked'
    assert len(discovery_http['posted']) == 1


@pytest.mark.parametrize('failure', ['not_configured', 'budget', 'provider_error'])
def test_web_index_failure_preserves_independent_native_items(discovery_client, discovery_http, monkeypatch, failure):
    client, factory, _, _ = discovery_client
    if failure == 'not_configured':
        monkeypatch.setattr(settings, 'dashscope_api_key', '')
    elif failure == 'budget':
        from app.agent_models import ModelUsage
        monkeypatch.setattr(settings, 'development_token_budget', 100000)
        with factory() as db:
            db.add(ModelUsage(tenant_id='t', property_id='p', job_id='previous', model='qwen-plus', input_tokens=100000, output_tokens=0))
            db.commit()
    else:
        discovery_http['status'] = 503
    success, row = execute_search(client, factory, platforms=['bilibili', 'douyin', 'xiaohongshu'])
    assert success, row.error
    result = row.result['discovery_search']
    assert result['total'] == 1 and result['items'][0]['platform'] == 'bilibili'
    sources = {source['platform']: source for source in result['sources']}
    assert sources['bilibili']['status'] == 'partial'
    assert sources['douyin']['status'] == 'not_connected'
    assert sources['xiaohongshu']['status'] == ('not_configured' if failure == 'not_configured' else 'blocked')
    assert sources['xiaohongshu']['reason']


@pytest.fixture
def douyin_public_read(monkeypatch):
    """Only the external browser read is replaced; queue/filter/qualification run."""
    from copy import deepcopy
    from app import douyin_discovery
    observed = datetime.now(timezone.utc).isoformat()
    state = {'status': 'partial', 'items': [
        {'platform': 'douyin', 'content_id': '7390000000000000001',
         'url': 'https://www.douyin.com/video/7390000000000000001', 'title': '苏州住宅大平层实拍',
         'author': '住宅实拍作者', 'published_at': (datetime.now(timezone.utc)-timedelta(days=3)).isoformat(),
         'metrics': {'views': None, 'likes': 4500, 'comments': 100, 'saves': 500, 'shares': 30},
         'metrics_observed_at': observed, 'metrics_source': 'douyin_public_search',
         'match_keywords': [], 'qualification': 'candidate_unverified', 'historical': False,
         'qualification_reason': '公开搜索结果候选', 'date_unknown': False, 'window_applied': False,
         'retrieved_at': observed, 'evidence_source': 'douyin_public_search'},
        {'platform': 'douyin', 'content_id': '7390000000000000002',
         'url': 'https://www.douyin.com/video/7390000000000000002', 'title': '苏州住宅采光与户型',
         'author': None, 'published_at': None,
         'metrics': {'views': None, 'likes': None, 'comments': None, 'saves': None, 'shares': None},
         'metrics_observed_at': None, 'metrics_source': None,
         'match_keywords': [], 'qualification': 'candidate_unverified', 'historical': False,
         'qualification_reason': '公开搜索结果候选，互动量与发布日期尚未核验',
         'date_unknown': True, 'window_applied': False, 'retrieved_at': observed},
    ]}
    def read(tenant_id, pid, request, cancelled=None):
        # A wrong project/profile or a lost worker cancellation hook would make
        # the browser boundary unsafe even if returned cards looked correct.
        assert (tenant_id, pid) == ('t', 'p')
        assert callable(cancelled)
        return deepcopy(state['items']), {'platform': 'douyin', 'adapter': 'douyin_public_browser',
            'status': state['status'], 'reason': '公共搜索首屏读取；累计互动量不代表近期增速'}
    monkeypatch.setattr(douyin_discovery, 'search_public_douyin', read)
    return state


def test_douyin_native_evidence_qualifies_metrics_and_keeps_unknown_date_separate(discovery_client, discovery_http, douyin_public_read):
    client, factory, _, _ = discovery_client
    success, row = execute_search(client, factory, platforms=['douyin'])
    assert success, row.error
    result = row.result['discovery_search']
    assert result['total'] == 2 and result['verified_count'] == 1
    verified, candidate = result['items']
    assert verified['content_id'] == '7390000000000000001'
    assert verified['qualification'] == 'verified_high_engagement'
    assert verified['metrics'] == {'views': None, 'likes': 4500, 'comments': 100, 'saves': 500, 'shares': 30}
    assert verified['metrics_source'] == 'douyin_public_search'
    assert verified['published_at'] and verified['window_applied'] is True
    assert '累计' in verified['qualification_reason']
    assert candidate['published_at'] is None and candidate['date_unknown'] is True
    assert candidate['window_applied'] is False and candidate['qualification'] == 'candidate_unverified'
    assert candidate['metrics'] == {'views': None, 'likes': None, 'comments': None, 'saves': None, 'shares': None}
    assert result['excluded_counts']['date_unknown'] == 1
    assert result['sources'][0]['adapter'] == 'douyin_public_browser'
    assert discovery_http['posted'] == [] and discovery_http['native_calls'] == []


def test_douyin_challenge_stops_with_empty_results_and_no_web_index_fallback(discovery_client, discovery_http, douyin_public_read):
    client, factory, _, _ = discovery_client
    douyin_public_read.update(status='challenge', items=[])
    success, row = execute_search(client, factory, platforms=['douyin'])
    assert success, row.error
    result = row.result['discovery_search']
    assert result['total'] == 0 and result['items'] == [] and result['verified_count'] == 0
    assert result['sources'][0]['status'] == 'challenge'
    assert result['sources'][0]['adapter'] == 'douyin_public_browser'
    assert discovery_http['posted'] == []


def test_douyin_only_unconnected_search_never_requires_or_bills_qwen(discovery_client, discovery_http, monkeypatch):
    from app.agent_models import ModelUsage
    client, factory, _, _ = discovery_client
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'http://127.0.0.1/private')
    monkeypatch.setattr(settings, 'development_token_budget', 100000)
    with factory() as db:
        db.add(ModelUsage(tenant_id='t', property_id='p', job_id='prior', model='qwen-plus', input_tokens=100000, output_tokens=0))
        db.commit()
    success, row = execute_search(client, factory, platforms=['douyin'])
    assert success, row.error
    result = row.result['discovery_search']
    assert result['total'] == 0 and result['sources'][0]['status'] == 'not_connected'
    assert result['sources'][0]['adapter'] == 'douyin_public_browser'
    assert discovery_http['posted'] == []
    with factory() as db:
        assert db.scalar(select(ModelUsage).where(ModelUsage.job_id == row.id)) is None
        assert db.scalar(select(ModelUsage).where(ModelUsage.job_id == 'prior')).input_tokens == 100000


def test_douyin_native_results_enforce_housing_city_and_publication_window(discovery_client, discovery_http, douyin_public_read):
    from copy import deepcopy
    client, factory, _, _ = discovery_client
    current = douyin_public_read['items'][0]
    wrong_city = deepcopy(current)
    wrong_city.update(content_id='7390000000000000003', url='https://www.douyin.com/video/7390000000000000003', title='杭州住宅大平层实拍')
    old = deepcopy(current)
    old.update(content_id='7390000000000000004', url='https://www.douyin.com/video/7390000000000000004',
               title='苏州住宅大平层历史案例', published_at=(datetime.now(timezone.utc)-timedelta(days=180)).isoformat())
    unrelated = deepcopy(current)
    unrelated.update(content_id='7390000000000000005', url='https://www.douyin.com/video/7390000000000000005', title='苏州猫咪搞笑合集')
    douyin_public_read['items'] = [current, wrong_city, old, unrelated]
    success, row = execute_search(client, factory, platforms=['douyin'], query='苏州 大平层', days=30)
    assert success, row.error
    result = row.result['discovery_search']
    assert [item['content_id'] for item in result['items']] == ['7390000000000000001']
    assert result['excluded_counts']['outside_window'] == 1
    assert result['excluded_counts']['irrelevant'] == 2
    assert result['verified_count'] == 1 and discovery_http['posted'] == []
