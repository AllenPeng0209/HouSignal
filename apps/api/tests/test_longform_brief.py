from datetime import datetime, timezone
import json

import pytest
from pydantic import ValidationError

from app.content_planning import PlanningConfig, PlanningSuggest, planning_generation_context
from app.video_breakdown import VideoBreakdownRequest
from app.research_workspace import ResearchNotes
from app.worker import claim_job, publish_progress
from app.job_models import Job
from test_worker_performance import queue_factory, add_job
from app.core_models import Membership
from app import model_usage_ledger as ledger
from fastapi import HTTPException
from test_video_breakdown import breakdown_client, preview
from app.reference_structure import compact_reference_beats
from test_content_planning import suggestions, model_reply


def test_longform_length_is_shared_by_planning_and_research_inputs():
    for seconds in (300,480,600):
        assert PlanningConfig(target_seconds=seconds).target_seconds == seconds
        assert PlanningSuggest(target_seconds=seconds).target_seconds == seconds
        assert ResearchNotes(target_seconds=seconds).target_seconds == seconds
        assert VideoBreakdownRequest(source_url='https://www.douyin.com/video/7390000000000000001',
            title='空间核对参考',transcript='[00:00-00:03] 提出看房问题',target_seconds=seconds).target_seconds==seconds
    for seconds in (61,120,299,601):
        with pytest.raises(ValidationError):
            PlanningConfig(target_seconds=seconds)


def test_reference_structure_beats_retain_evidence_and_function_without_transplanting_source_claims():
    source={'reference_id':'reference-1','source_url':'https://www.douyin.com/video/7390000000000000001',
        'title':'400㎡和888万元的原片', 'content_sha256':'a'*64,'evidence_basis':'asset_asr_and_sampled_frames',
        'research_notes':{},'analysis':{'job_id':'analysis-1','transcript_segments':[
            {'id':'t0','start_seconds':0,'end_seconds':3,'text':'400㎡值不值888万元？'},
            {'id':'t1','start_seconds':3,'end_seconds':25,'text':'走进厨房，再对照日常的家务动线。'}],
            'frames':[{'id':'f0','time_seconds':2,'observation':'问题字幕'}, {'id':'f1','time_seconds':10,'observation':'动线演示'}],
            'narrative_beats':[{'id':'b001','label':'问题引入','purpose':'提出日常需求问题',
                'summary':'用400㎡和888万元提出是否适合一家人生活的问题','evidence_refs':['t0','f0'],'start_seconds':0,'end_seconds':3,'status':'observed'},
                {'id':'b002','label':'过程演示','purpose':'把问题转为可核对的步骤','summary':'对照厨房与家务过程',
                    'evidence_refs':['t1','f1'],'start_seconds':3,'end_seconds':25,'status':'observed'}]}}
    context=planning_generation_context({'planning_reference':source})['reference']
    assert len(context['beats'])==2
    assert context['beats'][0]['id']=='b001'
    assert context['beats'][0]['evidence_refs']==['t0','f0']
    assert context['beats'][1]['end_seconds']==25
    text=json.dumps(context,ensure_ascii=False)
    assert '400㎡' not in text and '888万元' not in text
    assert '可核对' in text


def test_unanalyzed_reference_has_explicit_structure_hypotheses_without_fake_times_or_evidence():
    context=planning_generation_context({'planning_reference':{'reference_id':'r','source_url':'https://www.douyin.com/video/7390000000000000001',
        'title':'原片','content_sha256':'a'*64,'evidence_basis':'title_only','research_notes':{},'analysis':None}})['reference']
    assert context['beats']
    assert all(b['evidence_basis']=='structure_hypothesis' and b['start_seconds'] is None and not b['evidence_refs'] for b in context['beats'])


def test_long_reference_projection_preserves_later_evidence_within_chapter_context_budget():
    original=[{'id':f'b{i:03d}','label':'核对步骤','purpose':'理解判断依据','summary':'按问题核对证据',
        'start_seconds':i*9,'end_seconds':i*9+8,'evidence_refs':[f't{i}'],
        'evidence_basis':'observed_structure','status':'observed','analysis_job_id':'a','source_digest':'s'} for i in range(64)]
    projected=compact_reference_beats(original)
    assert len(projected)==12
    assert [beat for row in projected for beat in row['source_beat_ids']]==[row['id'] for row in original]
    assert projected[-1]['end_seconds']==575
    assert 't63' in projected[-1]['evidence_refs']
    assert all(row['status']=='observed' for row in projected)
    assert len(json.dumps(projected,ensure_ascii=False))<7000
    original[-1].update(status='hypothesis',evidence_basis='structure_hypothesis',evidence_refs=[],end_seconds=None,start_seconds=None)
    assert compact_reference_beats(original)[-1]['status']=='hypothesis'


@pytest.mark.parametrize('chapters',[6,10,13])
def test_long_suggestions_are_complete_and_can_be_adopted_by_planning_contract(chapters):
    from app.content_planning import _parse_candidates
    result=suggestions()
    for row in result['candidates']:
        row['outline']=['说明具体问题与核对过程']*chapters
    context={'verified_facts':[],'request':{'target_seconds':480}}
    if chapters>10:
        with pytest.raises(ValueError):_parse_candidates(model_reply(result),context)
    else:
        assert all(len(c['outline'])==chapters for c in _parse_candidates(model_reply(result),context))


def test_durable_longform_progress_never_publishes_drafts_or_uncommitted_content(queue_factory):
    jid=add_job(queue_factory,'generate_content')
    with queue_factory() as db:
        j=claim_job(db);token,attempt=j.lease_token,j.attempt
    counters={'completed_chapters':2,'total_chapters':6,'completed_shots':10,'total_shots':30,'target_seconds':300,'stage':'chapters'}
    publish_progress(jid,token,attempt,session_factory=queue_factory,progress=.35,stage='已完成2/6章',longform_progress=counters)
    with queue_factory() as db:
        assert db.get(Job,jid).result['longform_progress']==counters
        assert set(db.get(Job,jid).result)=={'longform_progress'}
    for invalid in ({**counters,'draft':'secret draft'},{**counters,'completed_chapters':7},{**counters,'total_shots':100000}):
        with pytest.raises(ValueError):
            publish_progress(jid,token,attempt,session_factory=queue_factory,progress=.4,stage='章节草稿',longform_progress=invalid)


@pytest.mark.parametrize('worker_metering',[False,True])
def test_render_editor_can_meter_actual_voice_calls_without_gaining_review_or_foreign_scope(queue_factory,worker_metering):
    jid=add_job(queue_factory,'render_content')
    with queue_factory() as db:
        job=claim_job(db)
        db.query(Membership).filter_by(user_id='u',tenant_id='t').one().role='editor';db.commit()
        if worker_metering:db.info['model_usage_recorder']=ledger.WorkerUsageRecorder(queue_factory,job)
        assert isinstance(ledger.begin_call(db,job),str)
        membership=db.query(Membership).filter_by(user_id='u',tenant_id='t').one()
        membership.role='reviewer';db.commit()
        with pytest.raises(HTTPException):ledger.begin_call(db,job)
        membership.role='editor';membership.project_ids=['other-project'];db.commit()
        with pytest.raises(HTTPException):ledger.begin_call(db,job)


def test_long_reference_adaptation_produces_a_chapter_brief_not_four_stretched_shots(breakdown_client):
    client,_,_,_=breakdown_client
    response=preview(client,target_seconds=480)
    assert response.status_code==200,response.text
    body=response.json()
    assert len(body['adaptation']['chapters'])==8
    assert sum(c['target_seconds'] for c in body['adaptation']['chapters'])==480
    assert body['adaptation']['shots']==[]
    assert body['adaptation']['needs_chapter_expansion'] is True
    assert body['plan_seed']['planning']['target_seconds']==480
    assert len(body['plan_seed']['planning']['outline'])==8

