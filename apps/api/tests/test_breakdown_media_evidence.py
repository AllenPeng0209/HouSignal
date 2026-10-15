"""Actual ASR/sample-frame evidence drives adaptation without crossing fact scopes."""
import hashlib
from pathlib import Path
import uuid

import pytest

from app.core_models import Asset
from app.job_models import Job
from app.operations_models import ContentOpportunity, ContentPlan
from test_video_breakdown import breakdown_client, ROOT


@pytest.fixture
def analyzed_reference(breakdown_client, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    client, db, current, ctx = breakdown_client
    raw = b'private reference bytes; decoding belongs to the analyzer tests'
    (tmp_path/'reference.mp4').write_bytes(raw)
    asset = Asset(id='reference-video', tenant_id='t', property_id='p', name='参考原片', kind='video',
        mime_type='video/mp4', storage_key='reference.mp4', byte_size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), rights='authorized', rights_revision=2,
        rights_evidence='仅研究并允许AI识别', origin_type='reference',
        metadata_json={'model_processing_authorized':True,'usage_scope':'research_only'})
    reference = ContentOpportunity(id='ref', tenant_id='t', property_id='p', title='真实原片资料',
        kind='competitor', platform='抖音', source_url='https://www.douyin.com/video/7390000000000000001',
        summary='', status='saved', media_asset_id=asset.id, revision=2)
    analysis = {'schema_version':2, 'job_id':'analysis', 'reference_id':'ref','reference_revision':2,
        'source':{'url':reference.source_url,'title':reference.title,'watched':False,'evidence_basis':'asset_asr_and_sampled_frames'},
        'asset_id':asset.id,'asset_sha256':asset.sha256,'rights_revision':2,'duration_seconds':10,
        'transcript_segments':[{'id':'t001','start_seconds':.25,'end_seconds':2.75,'text':'为什么888万元买400平方米？'}],
        'frames':[{'id':'f001','time_seconds':.125,'observation':'画面出现玄关，字幕有888万元'}],
        'techniques':[{'id':'question','label':'问题引入','reason':'带问题的开场','evidence_refs':['t001','f001']}],
        'summary':'用户授权原片的采样研究','coverage':{'sampled_frame_count':1,'note':'仅分析1个采样瞬间'},
        'model':'vision-test', 'usage_receipts':[]}
    job = Job(id='analysis',tenant_id='t',property_id='p',user_id='u',kind='reference_analyze',status='succeeded',
        payload={'reference_id':'ref','reference_revision':2,'asset_id':asset.id,'asset_sha256':asset.sha256,
                 'asset_byte_size':asset.byte_size,'rights_revision':2,'source_url':reference.source_url},
        result={'reference_id':'ref','reference_analysis':analysis})
    reference.analysis_snapshot=analysis
    db.add_all([asset,reference,job]); db.commit()
    return client,db,current,ctx,reference,asset,job


def media_preview(client, **overrides):
    return client.post(ROOT,json={'source_url':'https://www.douyin.com/video/7390000000000000001',
        'title':'真实原片资料','reference_id':'ref','analysis_job_id':'analysis','topic_id':'space',**overrides})


def test_analyzed_asset_supplies_exact_fractional_timing_and_evidence_basis(analyzed_reference):
    client,*_=analyzed_reference
    response=media_preview(client)
    assert response.status_code==200,response.text
    data=response.json()
    assert data['source']['evidence_basis']=='asset_asr_and_sampled_frames'
    assert data['source']['watched'] is False
    assert any(row['start_seconds']==.25 and row['end_seconds']==2.75 for row in data['timeline'])
    assert any(row['start_seconds']==.125 and row['visual'] for row in data['timeline'])
    target='\n'.join(row['voiceover'] for row in data['adaptation']['shots'])
    assert '120㎡' in target
    assert '888' not in target and '400' not in target
    assert '采样' in data['report']


def test_analysis_source_and_current_asset_authorization_are_rechecked(analyzed_reference):
    client,db,_,_,_,asset,_=analyzed_reference
    assert media_preview(client,source_url='https://www.douyin.com/video/7390000000000000002').status_code==409
    asset.rights='revoked';db.commit()
    assert media_preview(client).status_code==409


def test_reference_saved_notes_can_be_recovered_without_analysis(analyzed_reference):
    client,db,_,_,reference,_,_=analyzed_reference
    reference.research_notes={'transcript':'[00:00.250-00:02.750] 人工核对开场问题','observations':'','target_seconds':45,'topic_id':'space'}
    db.commit()
    response=media_preview(client,analysis_job_id=None,target_seconds=45)
    assert response.status_code==200,response.text
    assert response.json()['timeline'][0]['transcript']=='人工核对开场问题'
    assert response.json()['source']['evidence_basis']=='user_supplied_notes'


def test_plan_preserves_asset_evidence_and_reference_link(analyzed_reference):
    client,db,_,_,reference,asset,_=analyzed_reference
    data=media_preview(client).json()
    assert 'plan_seed' in data,data
    request={'reference':{'source_url':reference.source_url,'title':reference.title,
        'reference_id':'ref','analysis_job_id':'analysis','topic_id':'space'},
        'plan':data['plan_seed'],'idempotency_key':str(uuid.uuid4())}
    response=client.post(ROOT+'/plan',json=request)
    assert response.status_code==200,response.text
    plan=db.get(ContentPlan,response.json()['id'])
    assert plan.reference_id=='ref'
    snapshot=plan.source_snapshot['reference_analysis']
    assert snapshot['asset_provenance']['asset_id']==asset.id
    assert snapshot['asset_provenance']['asset_sha256']==asset.sha256
    assert snapshot['asset_analysis']['transcript_segments'][0]['start_seconds']==.25


def test_one_saved_reference_can_create_distinct_original_adaptations(analyzed_reference):
    client,db,_,_,reference,_,_=analyzed_reference
    data=media_preview(client).json()
    payload={'reference':{'source_url':reference.source_url,'title':reference.title,
        'reference_id':'ref','analysis_job_id':'analysis','topic_id':'space'},
        'plan':data['plan_seed'],'opportunity_id':'ref','idempotency_key':str(uuid.uuid4())}
    first=client.post(ROOT+'/plan',json=payload)
    assert first.status_code==200,first.text
    second=client.post(ROOT+'/plan',json={**payload,'idempotency_key':str(uuid.uuid4()),
        'plan':{**payload['plan'],'title':'同一结构的另一条原创改编'}})
    assert second.status_code==200,second.text
    assert first.json()['id']!=second.json()['id']
    assert db.get(ContentPlan,second.json()['id']).reference_id=='ref'


def test_fractional_manual_notes_are_not_rounded_or_inferred(breakdown_client):
    client,*_=breakdown_client
    response=client.post(ROOT,json={'source_url':'https://www.douyin.com/video/7390000000000000001',
        'title':'人工对照','transcript':'[00:00.250-00:02.750] 开场','topic_id':'space'})
    assert response.status_code==200,response.text
    assert response.json()['timeline'][0]['start_seconds']==.25


def test_selected_notes_can_adapt_long_source_without_projecting_entire_transcript(analyzed_reference):
    client,db,_,_,_,_,job=analyzed_reference
    analysis=dict(job.result['reference_analysis'])
    analysis['transcript_segments']=[{'id':f't{i:03d}','start_seconds':i,'end_seconds':i+.5,
        'text':'原片研究资料'*330} for i in range(8)]
    job.result={'reference_id':'ref','reference_analysis':analysis};db.commit()
    response=media_preview(client,transcript='[00:00.250-00:02.750] 只选关键问题',
                           observations='[00:00.125] 只选这个画面')
    assert response.status_code==200,response.text
    assert any(row['transcript']=='只选关键问题' for row in response.json()['timeline'])


def test_unselected_overlong_source_reports_input_limit_instead_of_server_error(analyzed_reference):
    client,db,_,_,_,_,job=analyzed_reference
    analysis=dict(job.result['reference_analysis'])
    analysis['transcript_segments']=[{'id':f't{i:03d}','start_seconds':i,'end_seconds':i+.5,
        'text':'原片研究资料'*330} for i in range(8)]
    job.result={'reference_id':'ref','reference_analysis':analysis};db.commit()
    with client.__class__(client.app,raise_server_exceptions=False) as scoped:
        assert media_preview(scoped).status_code==422
