"""Reference originals are private research material, never marketing footage."""
from dataclasses import replace
import hashlib
import importlib.util

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core_models import Asset
from app.job_models import Job
from app.operations_models import ContentOpportunity
from test_operations import operations_client
from test_business import scenario


@pytest.fixture
def upload_client(operations_client, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    client, db, current, ctx = operations_client
    if importlib.util.find_spec('app.reference_upload'):
        from app.reference_upload import router
        client.app.include_router(router)
    reference = ContentOpportunity(id='ref', tenant_id='t', property_id='p', title='参考看房视频',
        kind='competitor', platform='抖音', source_url='https://www.douyin.com/video/7390000000000000001',
        summary='', status='saved', source_key='douyin:7390000000000000001')
    db.add(reference); db.commit()
    yield client, db, current, ctx, tmp_path


def upload(client, **data):
    return client.post('/api/projects/p/operations/research/references/ref/media',
        data={'expected_revision': '1', 'evidence': '允许本项目研究与AI分析，不用于营销成片',
              'model_processing_authorized': 'true', **data},
        files={'file': ('reference.mp4', b'\x00\x00\x00\x18ftypisom'+b'\x00'*24, 'video/mp4')})


def test_reference_upload_is_scoped_research_only_and_does_not_enqueue_fact_extraction(upload_client):
    client, db, _, _, _ = upload_client
    response = upload(client)
    assert response.status_code == 200, response.text
    data = response.json()
    asset = db.get(Asset, data['asset']['id'])
    reference = db.get(ContentOpportunity, 'ref')
    assert asset.origin_type == 'reference'
    assert asset.rights == 'authorized'
    assert asset.metadata_json['model_processing_authorized'] is True
    assert asset.metadata_json['usage_scope'] == 'research_only'
    assert reference.media_asset_id == asset.id
    assert reference.revision == 2
    assert db.scalar(select(Job)) is None
    assert data['reference']['id'] == 'ref'


def test_stale_upload_does_not_replace_reference_or_leave_file(upload_client):
    client, db, _, _, directory = upload_client
    assert upload(client, expected_revision='2').status_code == 409
    assert db.get(ContentOpportunity, 'ref').media_asset_id is None
    assert db.scalar(select(Asset)) is None
    assert not list(directory.rglob('*.mp4'))


def test_upload_requires_explicit_research_use_and_project_role(upload_client):
    client, _, current, ctx, _ = upload_client
    assert upload(client, evidence=' ').status_code == 422
    current['context'] = replace(ctx, role='viewer')
    assert upload(client).status_code == 403
    current['context'] = replace(ctx, tenant_id='other')
    assert upload(client).status_code == 404


@pytest.mark.parametrize('research_marker', ['origin', 'purpose'])
def test_reference_footage_cannot_pass_release_checks_even_in_test_project(scenario, research_marker):
    from app.business import validate_content_ready
    db, ctx, project, _, asset, content = scenario
    project.is_test = True
    if research_marker=='origin':
        asset.origin_type = 'reference'
    else:
        asset.metadata_json={'usage_scope':'research_only'}
    db.commit()
    with pytest.raises(HTTPException) as error:
        validate_content_ready(db, ctx, content)
    assert error.value.status_code == 409


def test_webm_reference_upload_recognizes_its_actual_container_type(upload_client):
    client,*_=upload_client
    response=client.post('/api/projects/p/operations/research/references/ref/media',
        data={'expected_revision':'1','evidence':'仅研究并允许AI处理','model_processing_authorized':'true'},
        files={'file':('reference.webm',b'\x1a\x45\xdf\xa3'+b'\x42\x82\x84webm'+b'\0'*24,'video/webm')})
    assert response.status_code==200,response.text
    assert response.json()['asset']['mime_type']=='video/webm'


def test_extracting_reference_text_never_fills_target_project_facts(scenario, tmp_path):
    from app.media import handle_extract
    db, _, _, fact, _, _ = scenario
    fact.status = 'unverified'; fact.value = ''; fact.source = ''
    raw = '参考项目建筑面积：999平方米'.encode()
    (tmp_path/'reference.txt').write_bytes(raw)
    asset = Asset(id='reference-document', tenant_id='tenant', property_id='p', name='参考资料',
        kind='document', storage_key='reference.txt', mime_type='text/plain',
        sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw), rights='authorized',
        rights_evidence='仅研究授权', origin_type='reference', metadata_json={'usage_scope':'research_only'})
    db.add(asset); db.commit()
    handle_extract(db, Job(id='extract-reference', tenant_id='tenant', property_id='p', user_id='admin',
                          kind='extract_asset', payload={'asset_id': asset.id}))
    db.refresh(fact)
    assert fact.value == ''
    assert fact.source == ''
    assert asset.metadata_json['fact_candidates'] == []
