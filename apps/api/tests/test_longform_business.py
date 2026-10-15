"""Long-form production remains scoped, resumable, and evidence-checked."""
from copy import deepcopy
import hashlib
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import operations, provider
from app.agent_models import ModelUsage
from app.business import generate, handle_generate, revise_content, validate_content_ready
from app.business_models import ContentVersion
from app.core_models import Asset
from app.job_models import Job
from test_business import scenario
from test_longform_generation import outline_reply, chapter_reply, model_response


@pytest.fixture
def long_plan(scenario, monkeypatch):
    db, ctx, *_ = scenario
    monkeypatch.setattr(provider.settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(provider.settings, 'dashscope_api_key', 'synthetic-key')
    monkeypatch.setattr(provider.settings, 'dashscope_chat_model', 'configured-project-model')
    body = {'title': '家庭的空间取舍', 'topic_id': 'space', 'planning': {'content_type': 'professional',
        'subject': '空间观察', 'target_seconds': 360, 'skill_ids': ['topic-evaluation', 'titles-and-hooks'],
        'hook': '看房先想家人的日常', 'outline': ['从入口观察回家动线', '分析公共空间与活动关系',
        '检查休息和工作的私密边界', '用家庭需求比较空间取舍'], 'cta': '带着日常问题整理看房清单'}}
    plan = operations.create_plan(db, ctx, 'p', body)
    db.commit()
    return plan


class NativeLongModel:
    def __init__(self, *, interrupted=None, after_response=None):
        self.calls=[]
        self.interrupted=interrupted
        self.after_response=after_response
    def __call__(self,messages,**kwargs):
        request=json.loads(messages[-1]['content'])
        if request.get('source_mode')=='project_records':
            self.calls.append(('fact_review',None))
            return model_response({'issues':[]})
        stage=request['stage']
        chapter=request.get('chapter',{})
        self.calls.append((stage,chapter.get('id')))
        if stage=='outline':
            output=outline_reply()
            for row in output['chapters']:row['reference_beat_ids']=[]
        else:
            if chapter['id']==self.interrupted:raise provider.ProviderError('测试章节中断')
            output=chapter_reply(int(chapter['id'][1:])-1)
            revisions={row['id']:row['revision'] for row in request['facts']}
            for row in output['shots']:
                row['reference_beat_ids']=[]
                for ref in row['fact_refs']:ref['revision']=revisions[ref['id']]
        if self.after_response:self.after_response(stage,chapter)
        return model_response(output)


def job(plan,jid='long-generation'):
    return Job(id=jid,user_id='admin',tenant_id='tenant',property_id='p',kind='generate_content',
               payload={'topic_id':'space','plan_id':plan['id']},attempt=1)


def test_real_handler_saves_rich_longform_and_binds_the_chosen_plan(scenario,long_plan,monkeypatch):
    model=NativeLongModel()
    monkeypatch.setattr(provider,'chat',model)
    monkeypatch.setattr(provider,'generate_content',lambda *_args,**_kwargs:pytest.fail('长内容不能进入1200-token短生成'))
    result=handle_generate(scenario[0],job(long_plan))
    content=scenario[0].get(ContentVersion,result['content_version_id'])
    assert content.storyboard['schema_version']==2
    assert len(content.storyboard['chapters'])==6
    assert len(content.storyboard['shots'])==36
    assert content.storyboard['planning_context']['plan_id']==long_plan['id']
    assert result['plan_id']==long_plan['id']
    assert len([row for row in model.calls if row[0]=='chapter'])==6
    assert len([row for row in model.calls if row[0]=='outline'])==1


def test_story_cards_stay_bindable_but_out_of_default_generation_pool(scenario):
    from app.business import source_assets
    db,ctx,project,*_=scenario
    card=Asset(id='story-card',tenant_id='tenant',property_id='p',name='章节图示',kind='image',
        storage_key='card.png',mime_type='image/png',sha256='card',byte_size=3,rights='authorized',
        rights_evidence='项目图示制作授权',origin_type='actual',metadata_json={'presentation':'story_card'})
    db.add(card);db.commit()
    assert 'story-card' not in {row.id for row in source_assets(db,ctx,project)}
    assert db.get(Asset,'story-card').rights=='authorized'


def test_production_chapters_receive_source_bound_review_before_checkpoint(scenario,long_plan,monkeypatch):
    model=NativeLongModel();monkeypatch.setattr(provider,'chat',model)
    handle_generate(scenario[0],job(long_plan))
    assert len([row for row in model.calls if row[0]=='fact_review'])==6


def test_unsupported_non_numeric_claim_is_rejected_by_chapter_review(scenario,long_plan,monkeypatch):
    original=NativeLongModel()
    def model(messages,**kwargs):
        request=json.loads(messages[-1]['content'])
        if request.get('source_mode')=='project_records':
            claim='这个客厅南北通透，全天采光。'
            return model_response({'issues':[{'kind':'unsupported_fact','quote':claim,
                'reason':'资料只核验面积，没有客厅朝向与全天采光来源。'}]})
        response=original(messages,**kwargs)
        if request['stage']=='chapter' and request['chapter']['id']=='c1':
            value=json.loads(response['choices'][0]['message']['content'])
            value['shots'][0]['voiceover']+='这个客厅南北通透，全天采光。'
            response=model_response(value)
        return response
    monkeypatch.setattr(provider,'chat',model)
    with pytest.raises(provider.ProviderError,match='事实|章节'):
        handle_generate(scenario[0],job(long_plan))
    assert scenario[0].scalar(select(ContentVersion).where(ContentVersion.series_id=='long-generation')) is None


def test_all_planned_shots_cannot_override_schematic_material_origin(scenario,long_plan,monkeypatch):
    db,_,project,_,asset,*_=scenario
    project.is_test=True;asset.origin_type='template';db.commit()
    original=NativeLongModel()
    def model(messages,**kwargs):
        request=json.loads(messages[-1]['content'])
        response=original(messages,**kwargs)
        if request.get('stage')=='chapter':
            value=json.loads(response['choices'][0]['message']['content'])
            for shot in value['shots']:
                shot['asset_id']=None;shot['production_status']='planned'
            if request['chapter']['id']=='c1':value['shots'][0]['voiceover']+='我们已经现场实拍真实样板间。'
            return model_response(value)
        return response
    monkeypatch.setattr(provider,'chat',model)
    with pytest.raises(provider.SyntheticOriginError):
        handle_generate(db,job(long_plan))


def test_long_plan_deduplicates_active_request_and_server_selects_failed_resume(scenario,long_plan,tmp_path):
    db,ctx,*_=scenario
    first=generate('p',{'topic_id':'space','plan_id':long_plan['id']},db,ctx)
    second=generate('p',{'topic_id':'space','plan_id':long_plan['id'],'resume_from_job_id':'foreign'},db,ctx)
    assert second['job_id']==first['job_id']
    current=db.get(Job,first['job_id']);current.status='failed';db.commit()
    from app.longform_generation import checkpoint_directory
    directory=checkpoint_directory('tenant','p',current.id)
    directory.mkdir(parents=True)
    (directory/'draft.json').write_text('{}')
    third=generate('p',{'topic_id':'space','plan_id':long_plan['id'],'resume_from_job_id':'foreign'},db,ctx)
    assert third['job_id']!=first['job_id']
    assert db.get(Job,third['job_id']).payload['resume_from_job_id']==first['job_id']


def test_completed_chapter_is_reused_after_failure_but_fact_revision_invalidates_resume(scenario,long_plan,monkeypatch):
    db,_,_,fact,*_=scenario
    old=job(long_plan,'interrupted-long');db.add(old);db.commit()
    first=NativeLongModel(interrupted='c2');monkeypatch.setattr(provider,'chat',first)
    with pytest.raises(provider.ProviderError,match='测试章节中断'):handle_generate(db,old)
    db.rollback()
    resumed=job(long_plan,'resumed-long');resumed.payload|={'resume_from_job_id':old.id};db.add(resumed);db.commit()
    second=NativeLongModel();monkeypatch.setattr(provider,'chat',second)
    result=handle_generate(db,resumed)
    assert [row for row in second.calls if row[0]!='fact_review']==[('chapter',f'c{i}') for i in range(2,7)]
    assert len([row for row in second.calls if row[0]=='fact_review'])==5
    assert db.get(ContentVersion,result['content_version_id']).storyboard['schema_version']==2
    db.rollback()
    fact.revision=2;db.commit()
    fresh=job(long_plan,'changed-fact-long');fresh.payload|={'resume_from_job_id':old.id};db.add(fresh);db.commit()
    third=NativeLongModel();monkeypatch.setattr(provider,'chat',third)
    result=handle_generate(db,fresh)
    assert [row for row in third.calls if row[0]!='fact_review']==[('outline',None)]+[('chapter',f'c{i}') for i in range(1,7)]
    assert db.get(ContentVersion,result['content_version_id']).fact_refs==[{'id':'f','revision':2}]


def test_longform_rechecks_fact_state_before_persisting_a_chapter(scenario,long_plan,monkeypatch):
    db,_,_,fact,*_=scenario
    def changed(stage,_chapter):
        if stage=='chapter':fact.status='pending';db.commit()
    monkeypatch.setattr(provider,'chat',NativeLongModel(after_response=changed))
    with pytest.raises((ValueError,HTTPException),match='事实|资料'):
        handle_generate(db,job(long_plan))
    assert db.scalar(select(ContentVersion).where(ContentVersion.series_id=='long-generation')) is None


def test_long_revision_keeps_v2_and_refuses_script_voiceover_drift(scenario,long_plan,monkeypatch):
    db,ctx,*_=scenario
    monkeypatch.setattr(provider,'chat',NativeLongModel())
    generated=handle_generate(db,job(long_plan))
    content=db.get(ContentVersion,generated['content_version_id']);db.commit()
    with pytest.raises(HTTPException,match='口播|分镜'):
        revise_content(db,ctx,'p',content.id,{'script':content.script+'\n不对应分镜的文字'})
    revised=revise_content(db,ctx,'p',content.id,{'script':content.script,'storyboard':deepcopy(content.storyboard)})
    assert db.get(ContentVersion,revised['id']).storyboard['schema_version']==2


def test_render_requires_ready_material_for_every_longform_shot(scenario,long_plan,monkeypatch):
    db,ctx,*_=scenario
    monkeypatch.setattr(provider,'chat',NativeLongModel())
    result=handle_generate(db,job(long_plan))
    content=db.get(ContentVersion,result['content_version_id'])
    board=deepcopy(content.storyboard)
    board['shots'][0]['asset_id']=None;board['shots'][0]['production_status']='planned'
    content.storyboard=board;db.commit()
    with pytest.raises(HTTPException,match='镜头|素材'):
        validate_content_ready(db,ctx,content,require_render=False)


def test_long_render_requires_current_authorized_narration_asset(scenario,long_plan,monkeypatch):
    from app.business import validate_render_snapshot,content_snapshot_hash
    from app.config import settings
    db,_,project,*_=scenario
    monkeypatch.setattr(provider,'chat',NativeLongModel())
    result=handle_generate(db,job(long_plan))
    content=db.get(ContentVersion,result['content_version_id']);db.commit()
    video_bytes=b'opaque test movie; decoding belongs to media acceptance'
    audio_bytes=b'opaque test narration; speech acceptance belongs to media'
    (settings.storage_root/'long.mp4').write_bytes(video_bytes)
    (settings.storage_root/'voice.mp3').write_bytes(audio_bytes)
    audio=Asset(id='voice',tenant_id='tenant',property_id='p',name='旁白',kind='audio',storage_key='voice.mp3',
        mime_type='audio/mpeg',sha256=hashlib.sha256(audio_bytes).hexdigest(),byte_size=len(audio_bytes),rights='authorized',
        rights_evidence='普通配音用途授权',origin_type='actual',rights_revision=1)
    video=Asset(id='long-render',tenant_id='tenant',property_id='p',name='长片',kind='video',storage_key='long.mp4',
        mime_type='video/mp4',sha256=hashlib.sha256(video_bytes).hexdigest(),byte_size=len(video_bytes),rights='authorized',
        rights_evidence='项目渲染授权',origin_type='actual',metadata_json={'renderer':'ffmpeg','content_version_id':content.id,
        'content_snapshot_sha256':content_snapshot_hash(db,content),'voiceover_audio_asset_id':'voice',
        'voiceover_audio_sha256':audio.sha256,'voiceover_audio_rights_revision':1,
        'narration_text_sha256':hashlib.sha256(content.script.encode()).hexdigest()})
    db.add_all([audio,video]);content.render_asset_id=video.id;db.commit()
    assert validate_render_snapshot(db,content,project).id=='long-render'
    audio.rights='revoked';audio.rights_revision=2;db.commit()
    with pytest.raises(HTTPException,match='配音|音频|旁白'):
        validate_render_snapshot(db,content,project)
