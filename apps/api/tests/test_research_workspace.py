"""Research APIs preserve evidence, scope and concurrent editorial drafts."""
from dataclasses import replace
import importlib.util

import pytest
from sqlalchemy import select

from test_operations import operations_client
from test_discovery import discovery_client

ROOT = '/api/projects/p/operations/research'


@pytest.fixture
def research_client(operations_client):
    client, db, current, ctx = operations_client
    if importlib.util.find_spec('app.research_workspace'):
        from app.research_workspace import router
        client.app.include_router(router)
    return client, db, current, ctx


def saved_search(client, **overrides):
    response = client.post(ROOT + '/saved-searches', json={'name': '全国住宅观察', 'filters': {
        'scope': {'geography': 'nationwide', 'cities': [], 'segments': ['residential', 'luxury', 'improvement'], 'topics': []},
        'platforms': ['bilibili'], 'days': 30, **overrides}})
    assert response.status_code == 200, response.text
    return response.json()


def manual(client, **overrides):
    response = client.post(ROOT + '/references', json={'source_url': 'https://www.bilibili.com/video/BV1Ab411c7XY',
        'title': '住宅实拍参考', **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def test_saved_national_scope_expands_or_queries_and_runs_persistently(research_client):
    client, db, _, _ = research_client
    row = saved_search(client)
    assert row['queries'] == ['房地产', '豪宅', '住宅 改善']
    assert row['filters']['schema_version'] == 1
    assert client.get(ROOT + '/saved-searches').json()[0]['id'] == row['id']
    result = client.post(ROOT + '/saved-searches/' + row['id'] + '/run')
    assert result.status_code == 200, result.text
    from app.job_models import Job
    job = db.get(Job, result.json()['job_id'])
    assert job.kind == 'discovery_search'
    assert job.payload['queries'] == ['房地产', '豪宅', '住宅 改善']
    assert job.payload['saved_search_id'] == row['id']
    assert job.payload['saved_search_revision'] == 1
    assert client.get(ROOT + '/runs').json()[0]['id'] == job.id


def test_new_research_search_includes_a_public_source_without_overwriting_saved_platform_choice(research_client, monkeypatch):
    client, db, _, _ = research_client
    from app.platform_runtime import runtime
    monkeypatch.setattr(runtime,'login_status',lambda *args:{'loggedIn':False,'verified':False})
    created = client.post(ROOT + '/saved-searches', json={'name':'全国住宅新研究'})
    assert created.status_code == 200
    row = created.json()
    assert row['filters']['platforms'] == ['douyin', 'bilibili']
    assert row['filters']['account_scope'] == 'operations'
    run = client.post(ROOT + '/saved-searches/' + row['id'] + '/run')
    from app.job_models import Job
    assert db.get(Job,run.json()['job_id']).payload['platforms'] == ['douyin','bilibili']
    explicit = saved_search(client,platforms=['douyin'])
    assert client.get(ROOT + '/saved-searches').json()[0]['filters']['platforms'] == ['douyin']


def test_saved_search_cas_prevents_lost_filters_and_rejects_bad_scope(research_client):
    client, _, _, _ = research_client
    row = saved_search(client)
    url = ROOT + '/saved-searches/' + row['id']
    assert client.patch(url, json={'expected_revision': 1, 'name': '豪宅与改善'}).json()['revision'] == 2
    assert client.patch(url, json={'expected_revision': 1, 'name': '覆盖较新编辑'}).status_code == 409
    assert client.post(ROOT + '/saved-searches', json={'name': '范围错误', 'filters': {
        'scope': {'geography': 'cities', 'cities': [], 'segments': ['residential'], 'topics': []}}}).status_code == 422
    assert client.post(ROOT + '/saved-searches', json={'name': '多余字段', 'filters': {'secret': 'no'}}).status_code == 422


def test_query_budget_reports_omitted_combinations_and_covers_cities_first(research_client):
    client, _, _, _ = research_client
    row = saved_search(client, scope={'geography': 'cities', 'cities': ['上海', '北京', '广州', '深圳', '杭州'],
        'segments': ['residential', 'luxury', 'improvement'], 'topics': ['layout', 'light']})
    assert len(row['queries']) == 6
    assert row['total_query_count'] == 30
    assert row['queries_truncated'] is True
    assert row['coverage_note']
    assert all(any(query.startswith(city + ' ') for query in row['queries']) for city in ['上海', '北京', '广州', '深圳', '杭州'])


# Catches dropping the chosen editorial purpose or adding its label to public search terms.
@pytest.mark.parametrize('content_type', ['professional', 'traffic', 'trust', 'acquisition', 'conversion'])
def test_content_type_is_persisted_as_research_metadata(research_client, content_type):
    client, db, _, _ = research_client
    row = saved_search(client, scope={'geography': 'cities', 'cities': ['苏州'],
        'segments': ['residential'], 'topics': ['layout'], 'content_type': content_type}, query='动线')
    assert row['queries'] == ['苏州 房地产 户型 动线']
    restored = client.get(ROOT + '/saved-searches').json()[0]
    assert restored['filters']['scope']['content_type'] == content_type
    run = client.post(ROOT + '/saved-searches/' + row['id'] + '/run')
    assert run.status_code == 200, run.text
    from app.job_models import Job
    job = db.get(Job, run.json()['job_id'])
    assert job.payload['queries'] == ['苏州 房地产 户型 动线']
    assert job.payload['research_filters']['scope']['content_type'] == content_type
    updated = client.patch(ROOT + '/saved-searches/' + row['id'], json={
        'expected_revision': 1, 'filters': restored['filters'] | {
            'scope': restored['filters']['scope'] | {'content_type': None}}})
    assert updated.status_code == 200, updated.text
    assert updated.json()['filters']['scope']['content_type'] is None
    assert updated.json()['queries'] == ['苏州 房地产 户型 动线']


# Catches accepting undefined categories that clients cannot restore or present consistently.
def test_unknown_content_type_cannot_create_a_research_direction(research_client):
    client, _, _, _ = research_client
    result = client.post(ROOT + '/saved-searches', json={'name': '错误类型', 'filters': {
        'scope': {'segments': ['residential'], 'content_type': 'unknown'}}})
    assert result.status_code == 422
    assert client.get(ROOT + '/saved-searches').json() == []


# Catches making pre-existing JSON directions unreadable or changing their search coverage.
def test_legacy_research_direction_without_content_type_still_runs(research_client):
    client, db, _, _ = research_client
    row = saved_search(client)
    from app.operations_models import DiscoverySavedSearch
    record = db.get(DiscoverySavedSearch, row['id'])
    legacy_scope = {key: value for key, value in record.filters['scope'].items() if key != 'content_type'}
    record.filters = record.filters | {'scope': legacy_scope}
    db.commit()
    restored = client.get(ROOT + '/saved-searches').json()[0]
    assert restored['filters']['scope'].get('content_type') is None
    assert restored['queries'] == ['房地产', '豪宅', '住宅 改善']
    run = client.post(ROOT + '/saved-searches/' + row['id'] + '/run')
    assert run.status_code == 200, run.text
    from app.job_models import Job
    job = db.get(Job, run.json()['job_id'])
    assert job.payload['research_filters']['scope']['content_type'] is None
    assert job.payload['queries'] == ['房地产', '豪宅', '住宅 改善']


def test_reference_save_resolves_server_job_evidence_and_deduplicates(research_client):
    client, db, _, _ = research_client
    from app.job_models import Job
    item = {'platform': 'bilibili', 'content_id': 'BV1Ab411c7XY', 'url': 'https://www.bilibili.com/video/BV1Ab411c7XY',
        'title': '真实住宅原片', 'metrics': {'likes': 1200, 'views': None}, 'metrics_source': 'bilibili_public_api',
        'metrics_observed_at': '2026-10-14T02:00:00+00:00', 'qualification': 'verified_high_engagement'}
    job = Job(tenant_id='t', property_id='p', user_id='u', kind='discovery_search', status='succeeded',
        payload={'query': '住宅'}, result={'discovery_search': {'items': [item]}})
    db.add(job)
    db.commit()
    body = {'job_id': job.id, 'platform': 'bilibili', 'content_id': 'BV1Ab411c7XY', 'tags': ['户型', '户型'], 'note': '研究开场'}
    first = client.post(ROOT + '/references', json=body)
    assert first.status_code == 200, first.text
    row = first.json()
    assert row['source_snapshot']['metrics']['likes'] == 1200
    assert row['tags'] == ['户型']
    assert row['source_key'] == 'bilibili:BV1Ab411c7XY'
    assert client.post(ROOT + '/references', json=body).json()['id'] == row['id']
    assert len(client.get(ROOT + '/references').json()) == 1
    assert client.post(ROOT + '/references', json=body | {'source_snapshot': {'metrics': {'likes': 999999}}}).status_code == 422
    plan = client.post('/api/projects/p/operations/opportunities/' + row['id'] + '/plan', json={'topic_id': 'tour'}).json()
    assert plan['source']['source_snapshot']['metrics']['likes'] == 1200


def test_reference_notes_recover_and_stale_edit_cannot_erase_newer_work(research_client):
    client, _, _, _ = research_client
    row = manual(client)
    url = ROOT + '/references/' + row['id']
    response = client.patch(url, json={'expected_revision': 1, 'curation_state': 'shortlisted',
        'research_notes': {'transcript': '[00:00-00:03] 住宅该怎么看？', 'observations': '开场户型图', 'target_seconds': 45, 'topic_id': 'layout'}})
    assert response.status_code == 200, response.text
    assert response.json()['revision'] == 2
    restored = client.get(url).json()
    assert restored['research_notes']['target_seconds'] == 45
    assert restored['next_action'] == 'analyze'
    assert client.patch(url, json={'expected_revision': 1, 'note': '旧页面覆盖'}).status_code == 409
    assert client.patch(url, json={'expected_revision': 2, 'source_url': 'https://www.bilibili.com/video/BV1Bb411c7XZ'}).status_code == 422
    updated = client.patch(url, json={'expected_revision': 2, 'research_notes': {'observations': '补充新画面'}}).json()
    assert updated['research_notes']['transcript'] == '[00:00-00:03] 住宅该怎么看？'
    assert updated['research_notes']['target_seconds'] == 45


def test_existing_manual_source_receives_first_verified_snapshot_without_overwriting_editorial_work(research_client):
    client, db, _, _ = research_client
    row = manual(client, note='保留编导笔记', title='团队命名')
    from app.job_models import Job
    job = Job(tenant_id='t', property_id='p', user_id='u', kind='discovery_search', status='succeeded', payload={},
        result={'discovery_search': {'items': [{'platform': 'bilibili', 'content_id': 'BV1Ab411c7XY',
            'url': row['source_url'], 'title': '平台住宅标题', 'metrics': {'likes': 2345}}]}})
    db.add(job)
    db.commit()
    response = client.post(ROOT + '/references', json={'job_id': job.id, 'platform': 'bilibili', 'content_id': 'BV1Ab411c7XY'})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated['id'] == row['id']
    assert (updated['title'], updated['note']) == ('团队命名', '保留编导笔记')
    assert updated['source_snapshot']['metrics']['likes'] == 2345


def test_reference_asset_requires_same_project_authorized_video(research_client):
    client, db, _, _ = research_client
    from app.core_models import Asset
    db.add_all([Asset(id='good', tenant_id='t', property_id='p', name='授权原片', kind='video', mime_type='video/mp4',
        storage_key='unused', sha256='a'*64, byte_size=20, rights='authorized', rights_evidence='创作者授权分析',
        origin_type='reference', metadata_json={'model_processing_authorized': True}),
        Asset(id='foreign', tenant_id='t', property_id='p2', name='其他项目', kind='video', mime_type='video/mp4',
        storage_key='unused', sha256='b'*64, byte_size=20, rights='authorized', rights_evidence='已授权',
        metadata_json={'model_processing_authorized': True}),
        Asset(id='unauthorized', tenant_id='t', property_id='p', name='未授权模型', kind='video', mime_type='video/mp4',
        storage_key='unused', sha256='c'*64, byte_size=20, rights='authorized', rights_evidence='仅保存', metadata_json={})])
    db.commit()
    row = manual(client)
    url = ROOT + '/references/' + row['id']
    for asset in ('foreign', 'unauthorized'):
        assert client.patch(url, json={'expected_revision': 1, 'media_asset_id': asset}).status_code in (404, 409)
    assert client.patch(url, json={'expected_revision': 1, 'media_asset_id': 'good'}).json()['media_asset_id'] == 'good'


def test_changing_source_asset_clears_old_analysis_and_keeps_manual_notes(research_client):
    client, db, _, _ = research_client
    from app.core_models import Asset
    from app.operations_models import ContentOpportunity
    db.add_all([Asset(id=identity, tenant_id='t', property_id='p', name=identity, kind='video', mime_type='video/mp4',
        storage_key='unused', sha256=char*64, byte_size=20, rights='authorized', rights_evidence='创作者授权分析',
        origin_type='reference', metadata_json={'model_processing_authorized': True}) for identity, char in [('clip1', 'a'), ('clip2', 'b')]])
    db.commit()
    row = manual(client)
    url = ROOT + '/references/' + row['id']
    response = client.patch(url, json={'expected_revision': 1, 'media_asset_id': 'clip1',
        'research_notes': {'transcript': '人工记录仍需保留'}}).json()
    record = db.get(ContentOpportunity, row['id'])
    record.analysis_snapshot = {'report': '第一条视频的分析'}
    db.commit()
    same = client.patch(url, json={'expected_revision': response['revision'], 'media_asset_id': 'clip1'}).json()
    assert same['analysis_snapshot']['report'] == '第一条视频的分析'
    replaced = client.patch(url, json={'expected_revision': same['revision'], 'media_asset_id': 'clip2'}).json()
    assert replaced['analysis_snapshot'] == {}
    assert replaced['research_notes']['transcript'] == '人工记录仍需保留'


def test_research_scope_and_mutation_role_hide_other_projects(research_client):
    client, _, current, ctx = research_client
    row = manual(client)
    current['context'] = replace(ctx, role='viewer')
    assert client.get(ROOT + '/references/' + row['id']).status_code == 200
    assert client.patch(ROOT + '/references/' + row['id'], json={'expected_revision': 1, 'note': 'no'}).status_code == 403
    current['context'] = replace(ctx, project_ids=['p2'])
    assert client.get(ROOT + '/references').status_code == 404
    current['context'] = replace(ctx, tenant_id='other')
    assert client.get(ROOT + '/references/' + row['id']).status_code == 404


def test_reference_derives_existing_plan_and_script_without_changing_legacy_status(research_client):
    client, db, _, _ = research_client
    row = manual(client)
    from app.operations_models import ContentPlan
    db.add(ContentPlan(id='plan', tenant_id='t', property_id='p', opportunity_id=row['id'], title='改编计划',
        channel='B站', topic_id='tour', source_snapshot={}, content_version_id=None))
    db.commit()
    result = client.get(ROOT + '/references/' + row['id']).json()
    assert (result['plan_id'], result['content_id'], result['next_action']) == ('plan', None, 'open_plan')
    assert result['status'] == 'saved'


def test_reference_opens_latest_of_multiple_adaptations(research_client):
    from datetime import datetime, timedelta, timezone
    client, db, _, _ = research_client
    row = manual(client)
    from app.operations_models import ContentPlan
    now = datetime.now(timezone.utc)
    db.add_all([ContentPlan(id='first', tenant_id='t', property_id='p', reference_id=row['id'], title='第一版改编',
        channel='B站', topic_id='tour', source_snapshot={}, created_at=now-timedelta(days=1)),
        ContentPlan(id='latest', tenant_id='t', property_id='p', reference_id=row['id'], title='第二版改编',
        channel='B站', topic_id='layout', source_snapshot={}, created_at=now)])
    db.commit()
    result = client.get(ROOT + '/references/' + row['id']).json()
    assert result['plan_id'] == 'latest'
    plan = client.get('/api/projects/p/operations/plans').json()
    assert all(item['reference_id'] == row['id'] for item in plan)


def test_concurrent_reference_save_has_one_identity_and_cas_has_one_winner(discovery_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.research_workspace import router
    client, _, _, _ = discovery_client
    client.app.include_router(router)
    gate = Barrier(4)
    def save(_):
        gate.wait()
        response = client.post(ROOT + '/references', json={'source_url': 'https://www.bilibili.com/video/BV1Ab411c7XY', 'title': '住宅参考'})
        return response.status_code, response.json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(save, range(4)))
    assert [status for status, _ in responses] == [200] * 4
    assert len({row['id'] for _, row in responses}) == 1
    row = responses[0][1]
    gate = Barrier(4)
    def edit(index):
        gate.wait()
        return client.patch(ROOT + '/references/' + row['id'], json={'expected_revision': 1, 'note': f'编辑{index}'}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(edit, range(4)))
    assert sorted(statuses) == [200, 409, 409, 409]
