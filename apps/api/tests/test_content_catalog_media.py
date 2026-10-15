"""Catalog thumbnails are persisted public evidence; reads never fetch media."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from test_content_catalog import ROOT, catalog_module, import_index, index_row, record_job, work
from test_research_workspace import research_client
from test_operations import operations_client


COVER = 'https://i0.hdslb.com/bfs/archive/real-public-cover.jpg'
PLAYER = 'https://player.bilibili.com/player.html?bvid=BV1Ab411c7XY&p=1&autoplay=0&poster=1'


def media_module():
    module = catalog_module()
    assert hasattr(module, 'enrich_catalog_media'), 'explicit backend media enrichment is missing'
    return module


def seed(db, ctx):
    from app.operations_models import ContentOpportunity
    import_index(catalog_module(), db, ctx, [index_row()])
    db.commit()
    return db.scalar(select(ContentOpportunity))


# Catches media metadata changing verified counters, classification, or editorial notes.
def test_enrichment_persists_only_media_and_get_reads_it_without_network(research_client, monkeypatch):
    client, db, _, ctx = research_client
    reference = seed(db, ctx)
    reference.note = '已有编导笔记'
    reference.research_notes = {'observations': '已有拆解'}
    reference.source_snapshot = reference.source_snapshot | work()
    original = deepcopy(reference.source_snapshot)
    db.commit()
    module = media_module()
    monkeypatch.setattr(module, '_bilibili_media', lambda identity: {
        'platform': 'bilibili', 'content_id': identity, 'cover_url': COVER,
        'source': 'bilibili_public_api', 'observed_at': '2026-10-14T09:00:00+00:00'})
    result = module.enrich_catalog_media(db, ctx, 'p', limit=15)
    db.commit()
    assert result == {'attempted': 1, 'enriched': 1, 'unavailable': 0}
    db.expire_all()
    saved = db.get(type(reference), reference.id)
    assert saved.source_snapshot['catalog_media']['cover_url'] == COVER
    assert {key: value for key, value in saved.source_snapshot.items() if key != 'catalog_media'} == original
    assert (saved.note, saved.research_notes) == ('已有编导笔记', {'observations': '已有拆解'})
    monkeypatch.setattr(module, '_bilibili_media', lambda identity: pytest.fail('GET must not fetch media'))
    item = client.get(ROOT + '/catalog').json()['categories'][0]['items'][0]
    assert item['cover_url'] == COVER and item['player_url'] == PLAYER
    assert item['metrics']['likes'] == 1200 and item['catalog_only'] is True


# Catches older records disappearing or constructing a player from a supplied arbitrary URL.
def test_legacy_catalog_has_nullable_cover_and_player_from_saved_canonical_identity(research_client):
    client, db, _, ctx = research_client
    reference = seed(db, ctx)
    reference.source_snapshot = reference.source_snapshot | {'player_url': 'https://evil.test/player'}
    db.commit()
    item = client.get(ROOT + '/catalog').json()['categories'][0]['items'][0]
    assert item.get('cover_url') is None
    assert item.get('player_url') == PLAYER


# Catches a newer measurement job suppressing a separately enriched reference thumbnail.
def test_newer_job_measurements_keep_persisted_reference_cover(research_client):
    client, db, _, ctx = research_client
    reference = seed(db, ctx)
    reference.source_snapshot = reference.source_snapshot | {'catalog_media': {
        'platform': 'bilibili', 'content_id': 'BV1Ab411c7XY', 'cover_url': COVER,
        'source': 'bilibili_public_api', 'observed_at': '2026-10-14T09:00:00+00:00'}}
    record_job(db, 'professional', stamp='2026-10-14T08:00:00+00:00', items=[work(metrics={'likes': 3210})])
    db.commit()
    item = client.get(ROOT + '/catalog').json()['categories'][0]['items'][0]
    assert item.get('cover_url') == COVER and item['metrics']['likes'] == 3210


# Catches unsafe stored thumbnails being exposed as a browser URL.
@pytest.mark.parametrize('url', [
    'http://i0.hdslb.com/bfs/archive/a.jpg', 'https://evil.test/a.jpg',
    'https://i0.hdslb.com.evil.test/a.jpg', 'https://user:password@i0.hdslb.com/a.jpg',
    'https://127.0.0.1/a.jpg', 'https://i0.hdslb.com:8443/a.jpg',
    'https://i0.hdslb.com/a.jpg\n', 'javascript:alert(1)', 'https://i0.hdslb.com/a.jpg?token=secret',
])
def test_catalog_rejects_unsafe_cover_urls(research_client, url):
    client, db, _, ctx = research_client
    reference = seed(db, ctx)
    reference.source_snapshot = reference.source_snapshot | {'cover_url': url}
    db.commit()
    item = client.get(ROOT + '/catalog').json()['categories'][0]['items'][0]
    assert 'cover_url' in item and item['cover_url'] is None


# Catches metadata for another video being applied to this video.
def test_mismatched_stored_media_identity_is_ignored(research_client):
    client, db, _, ctx = research_client
    reference = seed(db, ctx)
    reference.source_snapshot = reference.source_snapshot | {'catalog_media': {
        'platform': 'bilibili', 'content_id': 'BV1Bb411c7XZ', 'cover_url': COVER}}
    db.commit()
    item = client.get(ROOT + '/catalog').json()['categories'][0]['items'][0]
    assert 'cover_url' in item and item['cover_url'] is None


# Catches a blocked source inventing media, or mutation beyond the caller's project.
def test_unavailable_media_stays_unknown_and_other_projects_are_not_enriched(research_client, monkeypatch):
    client, db, _, ctx = research_client
    reference = seed(db, ctx)
    from app.operations_models import ContentOpportunity
    foreign = ContentOpportunity(tenant_id='t', property_id='p2', title='其他项目', kind='catalog',
        platform='B站', source_url=work()['url'], source_snapshot=deepcopy(reference.source_snapshot))
    db.add(foreign)
    db.commit()
    original = deepcopy(reference.source_snapshot)
    module = media_module()
    monkeypatch.setattr(module, '_bilibili_media', lambda identity: None)
    assert module.enrich_catalog_media(db, ctx, 'p') == {'attempted': 1, 'enriched': 0, 'unavailable': 1}
    db.commit()
    assert reference.source_snapshot == original and foreign.source_snapshot == original
    assert client.get(ROOT + '/catalog').json()['categories'][0]['items'][0]['cover_url'] is None
    for invalid in (replace(ctx, role='viewer'), replace(ctx, tenant_id='other'), replace(ctx, project_ids=['p2'])):
        with pytest.raises(HTTPException) as exc:
            module.enrich_catalog_media(db, invalid, 'p')
        assert exc.value.status_code in (403, 404)


# Catches enrichment replacing a concurrently saved row with stale ORM evidence.
def test_enrichment_reloads_row_after_network_before_merging(research_client, monkeypatch):
    _, db, _, ctx = research_client
    reference = seed(db, ctx)
    original_revision = reference.revision
    saved_snapshot = reference.source_snapshot | work(metrics={'likes': 7654}) | {'catalog_only': False}
    module = media_module()

    def fetched(identity):
        db.execute(update(type(reference)).where(type(reference).id == reference.id).values(
            source_snapshot=saved_snapshot, revision=original_revision + 1, kind='trend', status='saved',
            note='另一页面的新笔记', research_notes={'observations': '新拆解'})
            .execution_options(synchronize_session=False))
        db.commit()
        return {'platform': 'bilibili', 'content_id': identity, 'cover_url': COVER,
            'source': 'bilibili_public_api', 'observed_at': '2026-10-14T09:00:00+00:00'}

    monkeypatch.setattr(module, '_bilibili_media', fetched)
    module.enrich_catalog_media(db, ctx, 'p')
    db.commit()
    db.expire_all()
    saved = db.get(type(reference), reference.id)
    assert saved.source_snapshot['catalog_only'] is False
    assert saved.source_snapshot['metrics']['likes'] == 7654
    assert saved.source_snapshot['catalog_media']['cover_url'] == COVER
    assert saved.revision == original_revision + 2
    assert (saved.note, saved.research_notes) == ('另一页面的新笔记', {'observations': '新拆解'})


# Catches holding an already enriched row lock across the next network request.
def test_enrichment_fetches_all_media_before_changing_any_reference(research_client, monkeypatch):
    _, db, _, ctx = research_client
    from app.operations_models import ContentOpportunity
    import_index(catalog_module(), db, ctx, [index_row(), index_row(
        url='https://www.bilibili.com/video/BV1Bb411c7XZ')])
    db.commit()
    references = db.scalars(select(ContentOpportunity)).all()
    initial_revisions = {row.id: row.revision for row in references}
    module = media_module()

    def fetched(identity):
        # This assertion executes again during the second fetch. No row may
        # have been merged or flushed while remote requests remain pending.
        rows = db.execute(select(ContentOpportunity.id, ContentOpportunity.revision)).all()
        assert dict(rows) == initial_revisions
        return {'platform': 'bilibili', 'content_id': identity, 'cover_url': COVER,
            'source': 'bilibili_public_api', 'observed_at': '2026-10-14T09:00:00+00:00'}

    monkeypatch.setattr(module, '_bilibili_media', fetched)
    assert module.enrich_catalog_media(db, ctx, 'p') == {'attempted': 2, 'enriched': 2, 'unavailable': 0}
    db.commit()
    assert all(db.get(ContentOpportunity, key).revision == revision + 1
        for key, revision in initial_revisions.items())


# Catches repeated limited runs spending all requests on already covered rows.
def test_enrichment_skips_fifteen_existing_covers_and_reaches_sixteenth(research_client, monkeypatch):
    _, db, _, ctx = research_client
    from app.operations_models import ContentOpportunity
    identities = ['BV1Ab411c7X' + suffix for suffix in '123456789ABCDEFG']
    import_index(catalog_module(), db, ctx, [index_row(url='https://www.bilibili.com/video/' + identity)
        for identity in identities])
    db.commit()
    references = db.scalars(select(ContentOpportunity)).all()
    covered = {}
    for reference in references:
        identity = reference.source_snapshot['content_id']
        if identity != identities[-1]:
            reference.source_snapshot = reference.source_snapshot | {'catalog_media': {
                'platform': 'bilibili', 'content_id': identity, 'cover_url': COVER}}
            covered[reference.id] = reference.revision
    db.commit()
    module = media_module()

    def fetched(identity):
        assert identity == 'BV1Ab411c7XG'
        return {'platform': 'bilibili', 'content_id': identity, 'cover_url': COVER,
            'source': 'bilibili_public_api', 'observed_at': '2026-10-14T09:00:00+00:00'}

    monkeypatch.setattr(module, '_bilibili_media', fetched)
    assert module.enrich_catalog_media(db, ctx, 'p') == {'attempted': 1, 'enriched': 1, 'unavailable': 0}
    db.commit()
    assert all(db.get(ContentOpportunity, key).revision == revision for key, revision in covered.items())
    assert module.enrich_catalog_media(db, ctx, 'p') == {'attempted': 0, 'enriched': 0, 'unavailable': 0}


# Catches receiving a thumbnail from a different BVID or following a redirected page.
@pytest.mark.parametrize('kind', ['valid', 'wrong_identity', 'blocked', 'redirect'])
def test_fixed_public_api_media_validates_identity_and_unavailability(monkeypatch, kind):
    module = media_module()
    def respond(request):
        if request.url.host == 'api.bilibili.com':
            if kind == 'redirect':
                return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
            if kind == 'blocked':
                return httpx.Response(403)
            return httpx.Response(200, json={'code': 0, 'data': {
                'bvid': 'BV1Ab411c7XY' if kind == 'valid' else 'BV1Bb411c7XZ', 'pic': COVER}})
        return httpx.Response(403)
    factory = httpx.Client
    monkeypatch.setattr(module, '_catalog_media_client', lambda: factory(transport=httpx.MockTransport(respond)))
    result = module._bilibili_media('BV1Ab411c7XY')
    assert (result['cover_url'] if result else None) == (COVER if kind == 'valid' else None)


# Catches trusting og:image from a page whose canonical identity does not match.
@pytest.mark.parametrize('canonical', ['BV1Ab411c7XY', 'BV1Bb411c7XZ', None])
def test_public_html_fallback_requires_matching_canonical_video(monkeypatch, canonical):
    module = media_module()
    html = '<meta property="og:image" content="' + COVER + '">'
    if canonical:
        html += '<meta property="og:url" content="https://www.bilibili.com/video/' + canonical + '">'
    factory = httpx.Client
    def respond(request):
        if request.url.host == 'api.bilibili.com':
            return httpx.Response(403)
        return httpx.Response(200, text=html, headers={'content-type': 'text/html'})
    monkeypatch.setattr(module, '_catalog_media_client', lambda: factory(transport=httpx.MockTransport(respond)))
    result = module._bilibili_media('BV1Ab411c7XY')
    assert (result['cover_url'] if result else None) == (COVER if canonical == 'BV1Ab411c7XY' else None)


def media_script():
    path = Path(__file__).resolve().parents[3] / 'scripts' / 'enrich_content_catalog_media.py'
    assert path.exists(), 'explicit scoped media enrichment CLI is missing'
    spec = importlib.util.spec_from_file_location('enrich_content_catalog_media', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Catches an unscoped CLI or excessive fetches on a single invocation.
def test_media_cli_requires_explicit_scope_and_limit(research_client, monkeypatch, capsys):
    _, db, _, ctx = research_client
    from app.core_models import Membership
    db.add(Membership(user_id='u', tenant_id='t', role='content', active=True))
    seed(db, ctx)
    module = media_module()
    script = media_script()
    monkeypatch.setattr(script, 'SessionLocal', lambda: db)
    monkeypatch.setattr(module, '_bilibili_media', lambda identity: None)
    assert script.main(['--tenant-id', 't', '--project-id', 'p', '--user-id', 'u', '--limit', '1']) == 0
    assert json.loads(capsys.readouterr().out)['attempted'] == 1
    assert db.info['tenant_id'] == 't' and db.info['user_id'] == 'u'
    for arguments in ([], ['--tenant-id', 't'], ['--tenant-id', 't', '--project-id', 'p'],
        ['--tenant-id', 't', '--project-id', 'p', '--user-id', 'u', '--limit', '51']):
        with pytest.raises(SystemExit) as exc:
            script.main(arguments)
        assert exc.value.code == 2
