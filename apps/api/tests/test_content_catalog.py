"""The catalog only exposes persisted project evidence; refresh is an explicit backend action."""
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from test_research_workspace import research_client
from test_operations import operations_client

ROOT = '/api/projects/p/operations/research'


def work(identity='BV1Ab411c7XY', title='住宅购房政策解读', **extra):
    return {'platform': 'bilibili', 'content_id': identity,
        'url': 'https://www.bilibili.com/video/' + identity,
        'title': title, 'metrics': {'likes': 1200, 'views': None},
        'metrics_source': 'bilibili_public_api',
        'metrics_observed_at': '2026-10-14T02:00:00+00:00',
        'qualification': 'verified_high_engagement', **extra}


def record_job(db, category=None, *, items=None, status='succeeded', pid='p', tenant='t',
               stamp='2026-10-14T02:00:00+00:00', query='房地产 购房政策', sources=None):
    from app.job_models import Job
    row = Job(tenant_id=tenant, property_id=pid, user_id='u', kind='discovery_search', status=status,
        created_at=datetime.fromisoformat(stamp),
        payload={'query': query, 'research_filters': {'scope': {'content_type': category}}},
        result={'discovery_search': {'query': query, 'searched_at': stamp, 'items': items or [],
            'sources': sources or [{'platform': 'bilibili', 'status': 'ok'}]}})
    db.add(row)
    db.flush()
    return row


# Catches accidentally enqueueing online searches when users open the catalog.
def test_empty_catalog_has_five_directions_and_never_creates_jobs(research_client):
    client, db, _, _ = research_client
    from app.job_models import Job
    response = client.get(ROOT + '/catalog')
    assert response.status_code == 200, response.text
    result = response.json()
    assert [row['id'] for row in result['categories']] == [
        'professional', 'traffic', 'trust', 'acquisition', 'conversion']
    assert sum(len(row['subjects']) for row in result['categories']) == 19
    assert all(row['items'] == [] and row['collection_state'] == 'pending' for row in result['categories'])
    assert result['total'] == 0 and result['updated_at'] is None
    assert db.scalars(select(Job)).all() == []


# Catches tenant leakage, URL aliases yielding duplicates, and breaking reference saves.
def test_catalog_deduplicates_sources_and_preserves_latest_job_and_saved_reference(research_client):
    client, db, _, _ = research_client
    from app.operations_models import ContentOpportunity
    old = record_job(db, 'professional', items=[work(url='https://www.bilibili.com/video/BV1Ab411c7XY/?token=private')])
    latest = record_job(db, 'professional', stamp='2026-10-14T03:00:00+00:00', items=[work(
        metrics={'likes': 2345, 'views': None}, metrics_observed_at='2026-10-14T03:00:00+00:00')])
    record_job(db, 'professional', pid='p2', items=[work('BV1Bb411c7XZ', '其他项目住宅')])
    record_job(db, 'professional', tenant='other', items=[work('BV1Cb411c7XZ', '其他团队住宅')])
    record_job(db, 'professional', status='failed', stamp='2026-10-13T03:00:00+00:00', items=[work('BV1Db411c7XZ')])
    record_job(db, 'professional', items=[work('BV1Eb411c7XZ', url='https://evil.test/video/BV1Eb411c7XZ')])
    reference = ContentOpportunity(tenant_id='t', property_id='p', title='编辑保留标题', kind='trend',
        platform='B站', source_url=work()['url'], source_key='bilibili:BV1Ab411c7XY',
        source_snapshot=work() | {'job_id': old.id, 'search_query': '房地产 购房政策'})
    db.add(reference)
    db.commit()
    response = client.get(ROOT + '/catalog')
    assert response.status_code == 200, response.text
    result = response.json()
    category = result['categories'][0]
    assert len(category['items']) == result['total'] == 1
    item = category['items'][0]
    assert item['url'] == work()['url']
    assert item['metrics'] == {'views': None, 'likes': 2345, 'comments': None, 'saves': None, 'shares': None}
    assert item['job_id'] == latest.id and item['reference_id'] == reference.id
    assert item['subject'] == '购房政策' and item['subjects'] == ['购房政策']
    assert category['updated_at'] == '2026-10-14T03:00:00+00:00'
    saved = client.post(ROOT + '/references', json={'job_id': item['job_id'],
        'platform': item['platform'], 'content_id': item['content_id']})
    assert saved.status_code == 200 and saved.json()['id'] == reference.id


# Catches classifying every result from a multi-query batch as every subject or inventing a default category.
def test_catalog_uses_item_query_evidence_and_excludes_unclassified_or_manual_items(research_client):
    client, db, _, _ = research_client
    from app.operations_models import ContentOpportunity
    record_job(db, items=[work('BV1Ab411c7XY', '住宅房源对比', matched_queries=['房地产 房源对比'])], query='房地产')
    record_job(db, items=[work('BV1Bb411c7XZ', '住宅户型分析')], query='房地产 户型分析')
    record_job(db, items=[work('BV1Cb411c7XZ', '住宅某个无关选题')], query='房地产')
    db.add(ContentOpportunity(tenant_id='t', property_id='p', title='未核验住宅房源', kind='trend',
        platform='B站', source_url='https://www.bilibili.com/video/BV1Db411c7XZ', source_snapshot={}))
    db.commit()
    result = client.get(ROOT + '/catalog').json()
    assert result['total'] == 2
    assert result['categories'][0]['items'][0]['subject'] == '户型分析'
    assert result['categories'][4]['items'][0]['subject'] == '房源对比'


# Catches treating failed or source-blocked collections as finished with usable content.
def test_catalog_reports_in_progress_and_unavailable_collections_honestly(research_client):
    client, db, _, _ = research_client
    record_job(db, 'professional', status='running', items=[work()])
    record_job(db, 'traffic', status='failed')
    record_job(db, 'trust', sources=[{'platform': 'douyin', 'status': 'login_required'}])
    record_job(db, 'acquisition', sources=[{'platform': 'bilibili', 'status': 'ok'}])
    record_job(db, 'conversion', items=[work('BV1Bb411c7XZ', '住宅房源对比')], query='房地产 房源对比')
    db.commit()
    result = client.get(ROOT + '/catalog').json()
    assert [row['collection_state'] for row in result['categories']] == [
        'collecting', 'unavailable', 'unavailable', 'pending', 'ready']
    assert result['categories'][0]['items'] == []


# Catches broadening catalog reads past the same API project access checks as references.
def test_catalog_enforces_scope_and_allows_read_only_role(research_client):
    client, _, current, ctx = research_client
    current['context'] = replace(ctx, role='viewer')
    assert client.get(ROOT + '/catalog').status_code == 200
    current['context'] = replace(ctx, project_ids=['p2'])
    assert client.get(ROOT + '/catalog').status_code == 404
    current['context'] = replace(ctx, tenant_id='other')
    assert client.get(ROOT + '/catalog').status_code == 404


def catalog_module():
    assert importlib.util.find_spec('app.content_catalog') is not None, 'backend collection module is missing'
    from app import content_catalog
    return content_catalog


# Catches refreshing from frontend reads, omitted subject searches, and duplicate active jobs.
def test_backend_refresh_queues_nineteen_subjects_in_five_scoped_jobs_and_reuses_active(research_client):
    _, db, _, ctx = research_client
    module = catalog_module()
    from app.job_models import Job
    first = module.enqueue_catalog_refresh(db, ctx, 'p', platforms=['bilibili'])
    db.commit()
    assert len(first) == 5 and all(row['queued'] is True for row in first)
    jobs = db.scalars(select(Job)).all()
    assert len(jobs) == 5
    assert sum(len(row.payload['queries']) for row in jobs) == 19
    assert all((row.tenant_id, row.property_id, row.user_id) == ('t', 'p', 'u') for row in jobs)
    professional = next(row for row in jobs if row.payload['research_filters']['scope']['content_type'] == 'professional')
    assert professional.payload['queries'] == ['房地产 购房政策', '房地产 板块分析', '房地产 户型分析', '房地产 买房知识']
    assert all(len(row.payload['queries']) <= 6 and row.payload['platforms'] == ['bilibili'] for row in jobs)
    second = module.enqueue_catalog_refresh(db, ctx, 'p', platforms=['bilibili'])
    db.commit()
    assert {row['job_id'] for row in first} == {row['job_id'] for row in second}
    assert all(row['queued'] is False for row in second)
    assert len(db.scalars(select(Job)).all()) == 5


# Catches privileged refresh accidentally queuing other tenants or letting viewers mutate the queue.
def test_backend_refresh_checks_project_and_edit_role_before_queuing(research_client):
    _, db, _, ctx = research_client
    module = catalog_module()
    from app.job_models import Job
    for invalid in [replace(ctx, role='viewer'), replace(ctx, project_ids=['p2']), replace(ctx, tenant_id='other')]:
        with pytest.raises(HTTPException) as exc:
            module.enqueue_catalog_refresh(db, invalid, 'p', platforms=['bilibili'])
        assert exc.value.status_code in (403, 404)
    assert db.scalars(select(Job)).all() == []


def refresh_script():
    path = Path(__file__).resolve().parents[3] / 'scripts' / 'refresh_content_catalog.py'
    assert path.exists(), 'explicit scoped catalog refresh CLI is missing'
    spec = importlib.util.spec_from_file_location('refresh_content_catalog', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Catches a CLI defaulting to unscoped reads or forgetting context before project RLS reads.
def test_refresh_cli_requires_explicit_identity_and_queues_only_authorized_project(research_client, monkeypatch, capsys):
    _, db, _, _ = research_client
    from app.core_models import Membership
    from app.job_models import Job
    db.add(Membership(user_id='u', tenant_id='t', role='content', active=True))
    db.commit()
    script = refresh_script()
    monkeypatch.setattr(script, 'SessionLocal', lambda: db)
    assert script.main(['--tenant-id', 't', '--project-id', 'p', '--user-id', 'u', '--platforms', 'bilibili']) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result['jobs']) == 5
    assert db.info['tenant_id'] == 't' and db.info['user_id'] == 'u'
    assert {row.property_id for row in db.scalars(select(Job)).all()} == {'p'}
    assert 'ops@test.cn' not in json.dumps(result)
    for arguments in ([], ['--tenant-id', 't'], ['--tenant-id', 't', '--project-id', 'p']):
        with pytest.raises(SystemExit) as exc:
            script.main(arguments)
        assert exc.value.code == 2


# Catches accidentally using credentials for one tenant to submit jobs for a foreign project.
def test_refresh_cli_returns_safe_failure_without_enqueuing_foreign_project(research_client, monkeypatch, capsys):
    _, db, _, _ = research_client
    from app.core_models import Membership
    from app.job_models import Job
    db.add(Membership(user_id='u', tenant_id='t', role='content', active=True))
    db.commit()
    script = refresh_script()
    monkeypatch.setattr(script, 'SessionLocal', lambda: db)
    assert script.main(['--tenant-id', 't', '--project-id', 'foreign', '--user-id', 'u', '--platforms', 'bilibili']) == 1
    assert db.scalars(select(Job)).all() == []
    assert capsys.readouterr().err.strip() == '项目不存在或无权访问'


def index_row(**extra):
    return {'title': '住宅购房政策公开解读', 'url': 'https://www.bilibili.com/video/BV1Ab411c7XY?token=private',
        'content_type': 'professional', 'subject': '购房政策', 'author': '公开作者',
        'published_at': None, 'collected_at': '2026-10-14T05:00:00+00:00', 'summary': '公开网页检索到的房产选题。', **extra}


def import_index(module, db, ctx, rows):
    assert hasattr(module, 'import_catalog_index'), 'controlled public source index import is missing'
    return module.import_catalog_index(db, ctx, 'p', rows)


# Catches presenting web-index sources as verified platform metrics or cluttering personal collections.
def test_public_index_preserves_unknown_metrics_and_stays_out_of_reference_collection(research_client):
    client, db, _, ctx = research_client
    result = import_index(catalog_module(), db, ctx, [index_row()])
    db.commit()
    assert result['imported'] == 1
    assert client.get(ROOT + '/references').json() == []
    catalog = client.get(ROOT + '/catalog').json()
    item = catalog['categories'][0]['items'][0]
    assert catalog['total'] == 1 and catalog['categories'][0]['collection_state'] == 'ready'
    assert item['metrics'] == {'views': None, 'likes': None, 'comments': None, 'saves': None, 'shares': None}
    assert item['metrics_source'] == '公开网页索引' and item['metrics_observed_at'] is None
    assert item['qualification'] == 'candidate_unverified'
    assert item['url'] == work()['url'] and item['reference_id']
    assert item['catalog_only'] is True
    assert item['subject'] == '购房政策' and catalog['updated_at'] == '2026-10-14T05:00:00+00:00'


# Catches import dedup overwriting research notes or downgrading genuine API measurements.
def test_index_import_deduplicates_multi_category_sources_and_preserves_editorial_work(research_client):
    client, db, _, ctx = research_client
    from app.operations_models import ContentOpportunity
    reference = ContentOpportunity(tenant_id='t', property_id='p', title='编导自己的标题', kind='trend',
        platform='B站', source_url=work()['url'], source_key='bilibili:BV1Ab411c7XY', source_snapshot=work(),
        note='不可覆盖的笔记', research_notes={'observations': '已有拆解'}, analysis_snapshot={'result': '已有分析'})
    db.add(reference)
    db.commit()
    import_index(catalog_module(), db, ctx, [index_row(), index_row(content_type='traffic', subject='买房话题')])
    db.commit()
    restored = db.get(ContentOpportunity, reference.id)
    assert (restored.title, restored.note, restored.research_notes, restored.analysis_snapshot) == (
        '编导自己的标题', '不可覆盖的笔记', {'observations': '已有拆解'}, {'result': '已有分析'})
    assert restored.source_snapshot['metrics']['likes'] == 1200
    assert restored.source_snapshot['metrics_source'] == 'bilibili_public_api'
    assert restored.source_snapshot['catalog_types'] == ['professional', 'traffic']
    assert restored.source_snapshot['catalog_index']['metrics']['likes'] is None
    assert restored.source_snapshot.get('catalog_only') is not True
    assert len(client.get(ROOT + '/references').json()) == 1
    result = client.get(ROOT + '/catalog').json()
    assert result['categories'][0]['items'][0]['reference_id'] == reference.id
    assert result['categories'][0]['items'][0]['catalog_only'] is False
    assert result['categories'][1]['items'][0]['subject'] == '买房话题'


# Catches a new index timestamp making preserved platform measurements look freshly sampled.
@pytest.mark.parametrize('source_times,expected_collected', [
    ({'searched_at': '2026-10-14T02:00:00+00:00', 'retrieved_at': '2026-10-14T02:01:00+00:00'},
     '2026-10-14T02:00:00+00:00'),
    ({'searched_at': 'invalid', 'retrieved_at': '2026-10-14T02:01:00+00:00'},
     '2026-10-14T02:01:00+00:00'),
    ({'searched_at': None, 'retrieved_at': 'invalid'}, '2026-10-14T02:00:00+00:00'),
    ({'searched_at': None, 'retrieved_at': None, 'metrics_observed_at': None}, None),
])
def test_new_index_does_not_redate_existing_platform_evidence(research_client, source_times, expected_collected):
    client, db, _, ctx = research_client
    from app.operations_models import ContentOpportunity
    snapshot = work() | source_times | {
        'catalog_types': ['professional'], 'catalog_subjects': {'professional': ['购房政策']}}
    reference = ContentOpportunity(tenant_id='t', property_id='p', title='编导保留标题', kind='trend',
        platform='B站', source_url='https://www.bilibili.com/video/BV1Ab411c7XY',
        source_key='bilibili:BV1Ab411c7XY', source_snapshot=snapshot, note='保留我的研究笔记',
        research_notes={'observations': '保留画面拆解'})
    db.add(reference)
    db.commit()
    import_index(catalog_module(), db, ctx, [index_row(content_type='traffic', subject='买房话题',
        collected_at='2026-10-15T01:00:00+00:00')])
    db.commit()

    response = client.get(ROOT + '/catalog')
    assert response.status_code == 200, response.text
    categories = response.json()['categories']
    for category_id, expected_subject in [('professional', '购房政策'), ('traffic', '买房话题')]:
        item = next(category for category in categories if category['id'] == category_id)['items'][0]
        assert item['collected_at'] == expected_collected
        assert item['index_collected_at'] == '2026-10-15T01:00:00+00:00'
        assert item['metrics_observed_at'] == source_times.get('metrics_observed_at', '2026-10-14T02:00:00+00:00')
        assert item['metrics'] == {'views': None, 'likes': 1200, 'comments': None, 'saves': None, 'shares': None}
        assert item['metrics_source'] == 'bilibili_public_api'
        assert item['url'] == 'https://www.bilibili.com/video/BV1Ab411c7XY'
        assert item['reference_id'] == reference.id and item['subject'] == expected_subject
    saved = client.get(ROOT + '/references').json()[0]
    assert (saved['title'], saved['note'], saved['research_notes']) == (
        '编导保留标题', '保留我的研究笔记', {'observations': '保留画面拆解'})


# Catches overcorrecting by freezing the collection time of a genuinely index-only source.
def test_index_only_reimport_uses_latest_index_collection_time(research_client):
    client, db, _, ctx = research_client
    module = catalog_module()
    import_index(module, db, ctx, [index_row()])
    db.commit()
    import_index(module, db, ctx, [index_row(content_type='traffic', subject='买房话题',
        collected_at='2026-10-15T01:00:00+00:00')])
    db.commit()
    categories = client.get(ROOT + '/catalog').json()['categories']
    for category in categories[:2]:
        item = category['items'][0]
        assert item['collected_at'] == '2026-10-15T01:00:00+00:00'
        assert item['index_collected_at'] == '2026-10-15T01:00:00+00:00'
        assert item['metrics_observed_at'] is None
        assert item['metrics'] == {'views': None, 'likes': None, 'comments': None, 'saves': None, 'shares': None}
        assert item['metrics_source'] == '公开网页索引'


# Catches saving a displayed source being a no-op because it is already a hidden catalog row.
def test_explicitly_saving_catalog_source_adds_it_to_user_collection_without_losing_evidence(research_client):
    client, db, _, ctx = research_client
    import_index(catalog_module(), db, ctx, [index_row()])
    db.commit()
    before = client.get(ROOT + '/catalog').json()['categories'][0]['items'][0]
    saved = client.post(ROOT + '/references', json={'source_url': before['url'], 'title': before['title'], 'note': '我的选题笔记'})
    assert saved.status_code == 200, saved.text
    assert saved.json()['id'] == before['reference_id']
    assert saved.json()['source_snapshot'].get('catalog_only') is not True
    assert saved.json()['source_snapshot']['metrics_source'] == '公开网页索引'
    assert len(client.get(ROOT + '/references').json()) == 1
    catalog = client.get(ROOT + '/catalog').json()
    assert catalog['total'] == 1
    assert catalog['categories'][0]['items'][0]['catalog_only'] is False


# Catches an invalid or cross-scope index writing a partial catalog before rejecting the request.
def test_index_import_validates_all_sources_and_scope_before_writing(research_client):
    _, db, _, ctx = research_client
    module = catalog_module()
    from app.operations_models import ContentOpportunity
    for rows in ([index_row(), index_row(url='https://evil.test/video/BV1Ab411c7XY')],
                 [index_row(subject='不是该类选题')], [index_row(metrics={'likes': 999999})]):
        with pytest.raises(HTTPException) as exc:
            import_index(module, db, ctx, rows)
        assert exc.value.status_code == 422
    for invalid in (replace(ctx, role='viewer'), replace(ctx, tenant_id='other')):
        with pytest.raises(HTTPException) as exc:
            import_index(module, db, invalid, [index_row()])
        assert exc.value.status_code in (403, 404)
    assert db.scalars(select(ContentOpportunity)).all() == []


# Catches the index CLI also queuing native search jobs or neglecting project context.
def test_refresh_cli_can_import_controlled_index_without_online_jobs(research_client, monkeypatch, capsys, tmp_path):
    _, db, _, _ = research_client
    from app.core_models import Membership
    from app.job_models import Job
    db.add(Membership(user_id='u', tenant_id='t', role='content', active=True))
    db.commit()
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps([index_row()]), encoding='utf-8')
    script = refresh_script()
    monkeypatch.setattr(script, 'SessionLocal', lambda: db)
    result = script.main(['--tenant-id', 't', '--project-id', 'p', '--user-id', 'u', '--index-file', str(path)])
    assert result == 0
    assert json.loads(capsys.readouterr().out)['imported'] == 1
    assert db.scalars(select(Job)).all() == []


# Catches a catalog save discarding verified job evidence when an older index row already exists.
@pytest.mark.parametrize('already_measured', [False, True])
def test_saving_hidden_catalog_from_job_upgrades_only_index_evidence_and_keeps_research(research_client, already_measured):
    client, db, _, ctx = research_client
    from app.operations_models import ContentOpportunity
    import_index(catalog_module(), db, ctx, [index_row()])
    db.commit()
    reference = db.scalar(select(ContentOpportunity))
    reference.note = '已有编导笔记'
    reference.research_notes = {'observations': '已有画面拆解'}
    reference.analysis_snapshot = {'analysis': '已有分析'}
    index_metadata = {key: reference.source_snapshot[key] for key in ('catalog_types', 'catalog_subjects', 'catalog_index')}
    if already_measured:
        reference.source_snapshot = reference.source_snapshot | work()
    job = record_job(db, 'professional', items=[work(metrics={'likes': 4567, 'views': 10000})])
    db.commit()
    response = client.post(ROOT + '/references', json={'job_id': job.id, 'platform': 'bilibili', 'content_id': 'BV1Ab411c7XY'})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['source_snapshot']['catalog_only'] is False
    assert saved['source_snapshot']['metrics']['likes'] == (1200 if already_measured else 4567)
    assert saved['source_snapshot']['metrics_source'] == 'bilibili_public_api'
    if not already_measured:
        assert saved['source_snapshot']['job_id'] == job.id
    assert {key: saved['source_snapshot'][key] for key in index_metadata} == index_metadata
    assert (saved['note'], saved['research_notes'], saved['analysis_snapshot']) == (
        '已有编导笔记', {'observations': '已有画面拆解'}, {'analysis': '已有分析'})


# Catches an importer with a stale identity-map snapshot hiding a saved reference again or losing its newer evidence/revision.
def test_index_import_refreshes_stale_row_before_merging_a_concurrently_saved_reference(research_client):
    client, db, _, ctx = research_client
    from sqlalchemy import update
    from app.operations_models import ContentOpportunity
    module = catalog_module()
    import_index(module, db, ctx, [index_row()])
    db.commit()
    stale = db.scalar(select(ContentOpportunity))
    old_revision = stale.revision
    saved_snapshot = stale.source_snapshot | work(metrics={'likes': 7654, 'views': 20000}) | {
        'catalog_only': False, 'job_id': 'verified-job'}
    # Simulate another committed writer without refreshing this importer's
    # already-loaded ORM object. The next import must use fresh row evidence.
    db.execute(update(ContentOpportunity).where(ContentOpportunity.id == stale.id,
        ContentOpportunity.revision == old_revision).values(source_snapshot=saved_snapshot,
        revision=old_revision + 1, kind='trend', status='saved', note='另一页面已保存的笔记',
        research_notes={'observations': '另一页面的新拆解'}, analysis_snapshot={'report': '另一页面的新分析'})
        .execution_options(synchronize_session=False))
    db.commit()
    assert stale.source_snapshot['catalog_only'] is True and stale.revision == old_revision
    import_index(module, db, ctx, [index_row(content_type='traffic', subject='买房话题')])
    db.commit()
    db.expire_all()
    saved = db.get(ContentOpportunity, stale.id)
    assert saved.source_snapshot['catalog_only'] is False
    assert saved.revision == old_revision + 2
    assert saved.source_snapshot['metrics']['likes'] == 7654
    assert saved.source_snapshot['job_id'] == 'verified-job'
    assert saved.source_snapshot['catalog_types'] == ['professional', 'traffic']
    assert (saved.note, saved.research_notes, saved.analysis_snapshot) == (
        '另一页面已保存的笔记', {'observations': '另一页面的新拆解'}, {'report': '另一页面的新分析'})
    assert client.get(ROOT + '/references').json()[0]['id'] == saved.id


# Catches a later failed refresh making an already populated catalog appear unavailable.
@pytest.mark.parametrize('status,sources', [
    ('failed', None), ('succeeded', [{'platform': 'bilibili', 'status': 'blocked'}]),
    ('cancelled', None)])
def test_existing_catalog_remains_ready_when_latest_refresh_has_no_results(research_client, status, sources):
    client, db, _, _ = research_client
    record_job(db, 'professional', items=[work()])
    record_job(db, 'professional', status=status, sources=sources, stamp='2026-10-14T04:00:00+00:00')
    db.commit()
    category = client.get(ROOT + '/catalog').json()['categories'][0]
    assert len(category['items']) == 1
    assert category['collection_state'] == 'ready'


# Catches assigning simple property adverts to professional analysis from broad housing words alone.
@pytest.mark.parametrize('title', ['苏州新房户型三室两厅，欢迎预约看房',
    '苏州改善住宅动线顺畅，总价300万', '苏州新房采光好，房源在售'])
def test_generic_query_property_advert_does_not_imply_professional_analysis(research_client, title):
    client, db, _, _ = research_client
    record_job(db, items=[work(title=title)], query='房地产')
    db.commit()
    result = client.get(ROOT + '/catalog').json()
    assert result['categories'][0]['items'] == []
    assert result['total'] == 0


# Catches overcorrecting the heuristic by dropping explicit analysis or operator classifications.
def test_explicit_analysis_and_curated_content_types_still_assign_to_professional(research_client):
    client, db, _, ctx = research_client
    record_job(db, items=[work(title='住宅户型解析：家务动线如何规划')], query='房地产')
    record_job(db, 'professional', items=[work('BV1Bb411c7XZ', '新房户型三室两厅，欢迎预约看房')], query='房地产')
    import_index(catalog_module(), db, ctx, [index_row(url='https://www.bilibili.com/video/BV1Cb411c7XZ',
        title='新房采光好，欢迎看房', subject='户型分析')])
    db.commit()
    items = client.get(ROOT + '/catalog').json()['categories'][0]['items']
    assert {item['content_id'] for item in items} == {'BV1Ab411c7XY', 'BV1Bb411c7XZ', 'BV1Cb411c7XZ'}
    assert next(item for item in items if item['content_id'] == 'BV1Ab411c7XY')['subject'] == '户型分析'
    assert next(item for item in items if item['content_id'] == 'BV1Bb411c7XZ')['subject'] is None
    assert next(item for item in items if item['content_id'] == 'BV1Cb411c7XZ')['subject'] == '户型分析'
