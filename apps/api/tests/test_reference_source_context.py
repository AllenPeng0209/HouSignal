"""Reference evidence is stored for review, but generation receives structure only."""
import json

import pytest

from app import provider
from app.business import handle_generate
from app.job_models import Job
from test_business import scenario
from test_planned_generation import add_plan


def reference_snapshot():
    return {
        'source_url': 'https://www.douyin.com/video/7390000000000000001',
        'title': '原片价格888万元',
        'summary': '原片400平方米，收益999万元，均非当前项目事实',
        'reference_analysis': {
            'schema_version': 1,
            'content_sha256': 'a' * 64,
            'reference': {
                'source_url': 'https://www.douyin.com/video/7390000000000000001',
                'title': '原片价格888万元',
                'transcript': '[00:00-00:03] 为什么888万元买400㎡？',
                'observations': '[00:00-00:03] 标价888万元',
                'target_seconds': 30, 'topic_id': 'space',
            },
            'structure': {
                'known_clip_count': 1, 'untimed_clip_count': 0, 'last_known_seconds': 3, 'gaps': [],
                'opening_hypotheses': [{'label': '问题引入', 'evidence': '888万元买400㎡', 'status': 'hypothesis'}],
            },
            'reuse_template': {'id': 'question'},
        },
    }


def response():
    return {'topic': '空间', 'script': '420㎡的空间如何承接日常。', 'cover_title': '生活与空间',
            'fact_refs': [{'id': 'f', 'revision': 1}],
            'storyboard': [{'asset_id': 'a', 'duration': 30, 'start': 0, 'caption': '实拍空间'}]}


def test_reference_generation_projects_structure_without_original_property_claims(scenario, monkeypatch):
    db, *_ = scenario
    plan, _ = add_plan(scenario)
    plan.source_snapshot = reference_snapshot()
    db.commit()
    seen = []

    def model(data, **callbacks):
        seen.append(data)
        return response()

    monkeypatch.setattr(provider, 'generate_content', model)
    handle_generate(db, Job(id='reference-context', user_id='admin', tenant_id='tenant', property_id='p',
                           kind='generate_content', payload={'topic_id': 'space', 'plan_id': plan.id}))
    background = seen[0]['planning_context']['background']
    encoded = json.dumps(background, ensure_ascii=False)
    assert '888' not in encoded
    assert '400' not in encoded
    assert '999' not in encoded
    assert background['source_url'] == 'https://www.douyin.com/video/7390000000000000001'
    assert background['reference_analysis']['techniques'] == ['问题引入']
    assert background['reference_analysis']['template']['id'] == 'question'
    assert background['reference_analysis']['source_digest'] == 'a' * 64
    assert '原片价格888万元' in json.dumps(plan.source_snapshot, ensure_ascii=False)


def test_source_evidence_changed_during_generation_rejects_late_draft(scenario, monkeypatch):
    from fastapi import HTTPException
    db, *_ = scenario
    plan, _ = add_plan(scenario)
    plan.source_snapshot = reference_snapshot()
    db.commit()

    def model(data, **callbacks):
        changed = reference_snapshot()
        changed['reference_analysis']['content_sha256'] = 'b' * 64
        plan.source_snapshot = changed
        db.flush()
        return response()

    monkeypatch.setattr(provider, 'generate_content', model)
    with pytest.raises(HTTPException) as error:
        handle_generate(db, Job(id='reference-race', user_id='admin', tenant_id='tenant', property_id='p',
                               kind='generate_content', payload={'topic_id': 'space', 'plan_id': plan.id}))
    assert error.value.status_code == 409
    assert plan.content_version_id is None


def media_source_plan(scenario):
    import hashlib
    from app.config import settings
    from app.core_models import Asset
    from app.operations_models import ContentOpportunity
    db,*_=scenario
    plan,_=add_plan(scenario)
    raw=b'private authorized reference original'
    (settings.storage_root/'reference.mp4').write_bytes(raw)
    asset=Asset(id='source-video',tenant_id='tenant',property_id='p',name='参考',kind='video',
        mime_type='video/mp4',storage_key='reference.mp4',sha256=hashlib.sha256(raw).hexdigest(),
        byte_size=len(raw),rights='authorized',rights_evidence='研究许可',rights_revision=1,
        origin_type='reference',metadata_json={'model_processing_authorized':True})
    url='https://www.douyin.com/video/7390000000000000001'
    reference=ContentOpportunity(id='source-reference',tenant_id='tenant',property_id='p',
        title='参考',kind='competitor',platform='抖音',source_url=url,summary='',status='saved',media_asset_id=asset.id)
    analysis={'schema_version':2,'source':{'url':url},'reference_id':reference.id,
        'asset_id':asset.id,'asset_sha256':asset.sha256,'rights_revision':1,
        'transcript_segments':[],'frames':[]}
    job=Job(id='source-analysis',tenant_id='tenant',property_id='p',user_id='admin',kind='reference_analyze',
        status='succeeded',payload={'reference_id':reference.id,'source_url':url,'asset_id':asset.id,
        'asset_sha256':asset.sha256,'rights_revision':1},result={'reference_analysis':analysis})
    snapshot=reference_snapshot()
    snapshot['reference_analysis']['reference'].update(reference_id=reference.id,analysis_job_id=job.id)
    snapshot['reference_analysis']['asset_provenance']={'asset_id':asset.id,'job_id':job.id}
    plan.source_snapshot=snapshot
    db.add_all([asset,reference,job]);db.commit()
    return plan,asset


@pytest.mark.parametrize('revoke_when', ['before', 'during'])
def test_plan_generation_rechecks_reference_processing_permission(scenario, monkeypatch, revoke_when):
    from fastapi import HTTPException
    db,*_=scenario
    plan,asset=media_source_plan(scenario)
    if revoke_when=='before':
        asset.rights='revoked';db.commit()
    called=[]
    def model(data,**callbacks):
        called.append(True)
        if revoke_when=='during':
            asset.rights='revoked';db.flush()
        return response()
    monkeypatch.setattr(provider,'generate_content',model)
    with pytest.raises(HTTPException) as error:
        handle_generate(db,Job(id='reference-rights',tenant_id='tenant',property_id='p',user_id='admin',
            kind='generate_content',payload={'plan_id':plan.id,'topic_id':'space'}))
    assert error.value.status_code==409
    assert plan.content_version_id is None
    assert called==([] if revoke_when=='before' else [True])


def test_plain_saved_video_metadata_does_not_enter_target_generation_as_property_claims(scenario, monkeypatch):
    db,*_=scenario
    plan,_=add_plan(scenario)
    plan.source_snapshot={'source_key':'douyin:7390000000000000001',
        'source_url':'https://www.douyin.com/video/7390000000000000001',
        'summary':'参考视频说888万元400平方米','title':'888万元400平方米',
        'source_snapshot':{'title':'888万元400平方米','snippet':'其他楼盘999万元'}}
    db.commit();seen=[]
    def model(data,**callbacks):
        seen.append(data);return response()
    monkeypatch.setattr(provider,'generate_content',model)
    handle_generate(db,Job(id='reference-capture',tenant_id='tenant',property_id='p',user_id='admin',
        kind='generate_content',payload={'plan_id':plan.id,'topic_id':'space'}))
    projected=json.dumps(seen[0]['planning_context']['background'],ensure_ascii=False)
    assert all(value not in projected for value in ('888','400','999'))
    assert '7390000000000000001' in projected
