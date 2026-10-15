"""Contradictory capture language in an old plan cannot change schematic origins."""
from copy import deepcopy
import json
import stat

import pytest

from app import operations, provider
from app.business import handle_generate
from app.business_models import ContentVersion
from app.core_models import Asset
from app.job_models import Job
from app.jobs import job_dict
from test_business import scenario
from test_discovery import discovery_client
from test_content_planning import planning_client, queued, model_reply, suggestions


def schematic_plan(scenario):
    db, ctx, project, _, asset, _ = scenario
    project.is_test = True
    asset.origin_type = 'template'
    asset.metadata_json = {'description': '空间布局示意，不能证明现场拍摄'}
    plan = operations.create_plan(db, ctx, 'p', {'title': '420㎡真实样板间导览', 'topic_id': 'space',
        'brief': '所有内容来自授权实拍实测；已有示意说明，非实拍。', 'planning': {'content_type': 'conversion', 'target_seconds': 30,
            'skill_ids': [], 'hook': '我们用实拍和实测数据呈现空间。',
            'outline': ['用420㎡资料建立尺度', '展示授权实拍镜头'], 'cta': '了解户型资料'}})
    db.commit()
    return plan


def value(*, schematic=False, duration=30):
    return {'topic': '样板间示意导览' if schematic else '真实样板间导览',
        'script': '合成示意420平方米空间。' if schematic else '现场实拍420平方米空间。',
        'cover_title': '空间示意' if schematic else '现房实景',
        'fact_refs': [{'id': 'f', 'revision': 1}],
        'storyboard': [{'asset_id': 'a', 'caption': '合成示意空间' if schematic else '现场实拍空间',
                        'duration': duration, 'start': 0}]}


def generation(plan):
    return Job(id='schematic-generation', user_id='admin', tenant_id='tenant', property_id='p',
               kind='generate_content', payload={'topic_id': 'space', 'plan_id': plan['id']})


def test_generation_supplies_authoritative_basis_without_editing_original_plan(scenario, monkeypatch):
    plan = schematic_plan(scenario)
    original = deepcopy(plan)
    seen = []
    def model(data, **_callbacks):
        seen.append(data)
        assert data.get('material_basis', {}).get('kind') == 'schematic'
        assert data['material_basis']['real_capture_available'] is False
        assert data['planning_context']['expression_requirements'] == data['material_basis']['expression_requirements']
        assert '真实样板间' not in data['planning_context']['title']
        assert '实拍' not in data['planning_context']['planning']['hook']
        assert '非实拍' in data['planning_context']['brief']
        return value(schematic=True)
    monkeypatch.setattr(provider, 'generate_content', model)
    handle_generate(scenario[0], generation(plan))
    record = scenario[0].get(operations.ContentPlan, plan['id'])
    assert record.title == original['title']
    assert record.brief == original['brief']
    assert record.source_snapshot['planning']['hook'] == original['planning']['hook']


def test_schematic_basis_and_model_use_one_natural_disclosure(scenario, monkeypatch):
    plan=schematic_plan(scenario)
    disclosure='画面仅作空间示意，不代表实际房源。'
    seen=[]
    data=value(schematic=True)
    data['script']=disclosure+'420平方米空间资料。'
    def model(messages,**_kwargs):
        seen.append(messages)
        return model_reply(data)
    monkeypatch.setattr(provider,'chat',model)
    result=handle_generate(scenario[0],generation(plan))
    context=json.loads(seen[0][1]['content'])
    assert context['material_basis'].get('disclosure')==disclosure
    assert disclosure in seen[0][0]['content']
    assert disclosure in ' '.join(context['material_basis']['expression_requirements'])
    assert scenario[0].get(ContentVersion,result['content_version_id']).script.startswith(disclosure)


def test_generation_uses_source_assets_and_never_recursively_reuses_old_renders(scenario, monkeypatch):
    plan = schematic_plan(scenario)
    db, _, _, _, _, _ = scenario
    db.add_all([
        Asset(id='old-render', tenant_id='tenant', property_id='p', name='旧生成视频', kind='video',
            storage_key='old.mp4', mime_type='video/mp4', sha256='old', byte_size=3, rights='authorized',
            rights_evidence='渲染继承授权', origin_type='test', metadata_json={'renderer': 'ffmpeg', 'content_version_id': 'c'}),
        Asset(id='old-cover', tenant_id='tenant', property_id='p', name='旧生成封面', kind='image',
            storage_key='old.png', mime_type='image/png', sha256='old', byte_size=3, rights='authorized',
            rights_evidence='渲染继承授权', origin_type='test', metadata_json={'content_version_id': 'c'}),
    ])
    db.commit()
    seen = []
    def model(data, **_callbacks):
        seen.append(data)
        return value(schematic=True)
    monkeypatch.setattr(provider, 'generate_content', model)
    handle_generate(db, generation(plan))
    assert {row['id'] for row in seen[0]['assets']} == {'a'}


def test_real_uploaded_video_remains_a_source_asset(scenario, monkeypatch):
    db, _, _, _, _, _ = scenario
    db.add(Asset(id='uploaded-video', tenant_id='tenant', property_id='p', name='用户授权视频', kind='video',
        storage_key='uploaded.mp4', mime_type='video/mp4', sha256='uploaded', byte_size=3,
        rights='authorized', rights_evidence='上传与用途授权', origin_type='actual'))
    db.commit()
    seen = []
    def model(data, **_callbacks):
        seen.append(data)
        return value()
    monkeypatch.setattr(provider, 'generate_content', model)
    handle_generate(db, Job(id='actual-generation', user_id='admin', tenant_id='tenant', property_id='p',
        kind='generate_content', payload={'topic_id': 'space'}))
    assert {row['id'] for row in seen[0]['assets']} == {'a', 'uploaded-video'}


def test_manual_revision_cannot_bypass_schematic_claim_gate_on_render(scenario):
    from fastapi import HTTPException
    from app.business import validate_content_ready
    db, ctx, project, _, asset, content = scenario
    project.is_test = True
    asset.origin_type = 'template'
    content.script = '现场实拍420平方米空间。'
    db.commit()
    with pytest.raises(HTTPException, match='示意素材不能声称实拍'):
        validate_content_ready(db, ctx, content, require_render=False)


@pytest.mark.parametrize('script', [
    '所有画面为合成或示意，不代表现场实拍或真实样板间。420平方米空间资料。',
    '所有画面为合成或示意，不代表当前项目现场实拍或真实样板间。420平方米空间资料。',
    '合成示意，不代表实拍或现场记录。420平方米空间资料。',
    '合成示意，非实拍、现场记录或实测。420平方米空间资料。',
])
def test_negative_source_disclosure_covers_coordinated_claims(scenario, script):
    from app.business import validate_generated_content
    schematic_plan(scenario)
    data=value(schematic=True)
    data['script']=script
    assert validate_generated_content(scenario[0],scenario[1],'p',data)['script']==script


@pytest.mark.parametrize('script', [
    '所有画面为合成或示意，不代表现场实拍，但真实样板间可见420平方米空间。',
    '所有画面为合成或示意，不代表现场实拍，真实样板间可见420平方米空间。',
    '所有画面为合成或示意，不代表现场实拍或这里是现场记录420平方米空间。',
    '合成示意，非实拍，但这里是现场记录420平方米空间。',
    '合成示意，不代表实拍。这里现场记录420平方米空间。',
])
def test_negative_source_disclosure_does_not_cover_a_later_positive_claim(scenario, script):
    from app.business import validate_generated_content
    schematic_plan(scenario)
    data=value(schematic=True)
    data['script']=script
    with pytest.raises(provider.SyntheticOriginError):
        validate_generated_content(scenario[0],scenario[1],'p',data)


def test_source_failure_keeps_private_field_diagnostic_without_changing_user_error(scenario, monkeypatch):
    from app.config import settings
    plan=schematic_plan(scenario)
    bad=value(schematic=True)
    bad['script']='非实拍，但这里是现场记录420平方米空间。 api_key=synthetic-private-key'
    monkeypatch.setattr(provider,'chat',lambda *_args,**_kwargs:model_reply(bad))
    with pytest.raises(provider.SyntheticOriginError) as caught:
        handle_generate(scenario[0],generation(plan))
    error=caught.value
    assert getattr(error,'diagnostic_code',None)=='synthetic_origin_claim'
    assert getattr(error,'field',None)=='script'
    assert getattr(error,'matched_claim',None)=='现场记录'
    assert '现场记录' in error.excerpt
    assert 'synthetic-private-key' not in error.excerpt
    assert str(error)=='合成或示意素材不能声称实拍、现场记录或实测，请保留示意说明'
    receipt=settings.storage_root/'.private'/'generation-diagnostics'/'schematic-generation'/'source-expression.jsonl'
    assert receipt.is_file()
    assert stat.S_IMODE(receipt.stat().st_mode)==0o600
    rows=[json.loads(line) for line in receipt.read_text().splitlines()]
    assert rows[0]['field']=='script'
    assert rows[0]['matched_claim']=='现场记录'
    assert 'synthetic-private-key' not in receipt.read_text()


def test_schematic_capture_claim_is_corrected_once_by_model_and_fully_validated(scenario, monkeypatch):
    plan = schematic_plan(scenario)
    responses = iter([value(), value(schematic=True)])
    seen = []
    def model(messages, **_kwargs):
        seen.append(messages)
        return model_reply(next(responses))
    monkeypatch.setattr(provider, 'chat', model)
    result = handle_generate(scenario[0], generation(plan))
    content = scenario[0].get(ContentVersion, result['content_version_id'])
    assert content.script == '合成示意420平方米空间。'
    assert content.storyboard[0]['caption'] == '合成示意空间'
    assert content.fact_refs == [{'id': 'f', 'revision': 1}]
    assert len(seen) == 2
    assert scenario[0].get(operations.ContentPlan, plan['id']).title == '420㎡真实样板间导览'


@pytest.mark.parametrize('mutate', ['asset', 'fact_ref', 'duration', 'quantity'])
def test_source_wording_repair_cannot_change_frozen_evidence(scenario, monkeypatch, mutate):
    plan = schematic_plan(scenario)
    repaired = value(schematic=True)
    if mutate == 'asset':
        repaired['storyboard'][0]['asset_id'] = 'invented'
    elif mutate == 'fact_ref':
        repaired['fact_refs'][0]['revision'] = 2
    elif mutate == 'duration':
        repaired['storyboard'][0]['duration'] = 25
    else:
        repaired['script'] = '合成示意：400平方米空间资料。'
    responses = iter([value(), repaired])
    calls = []
    def model(messages, **_kwargs):
        calls.append(messages)
        return model_reply(next(responses))
    monkeypatch.setattr(provider, 'chat', model)
    with pytest.raises((ValueError, provider.ProviderError)):
        handle_generate(scenario[0], generation(plan))
    assert len(calls) == 2


def test_suggestion_receives_material_basis_and_rejects_capture_claims_in_candidate(planning_client, monkeypatch):
    from app.content_planning import handle_content_planning
    client, factory, _, _ = planning_client
    with factory() as db:
        from app.core_models import Property
        db.get(Property, 'p').is_test = True
        db.add(Asset(id='schematic', tenant_id='t', property_id='p', name='空间示意', kind='image',
            storage_key='schematic.png', mime_type='image/png', sha256='abc', byte_size=3,
            rights='authorized', rights_evidence='示意素材授权', origin_type='template'))
        db.commit()
    bad = suggestions()
    bad['candidates'][0]['hook'] = '我们用现场实拍呈现120㎡尺度'
    calls = []
    def model(messages, **_kwargs):
        calls.append(json.loads(messages[1]['content']))
        if len(calls)==2:
            assert '示意素材' in messages[-1]['content']
        return model_reply(bad if len(calls) == 1 else suggestions())
    monkeypatch.setattr(provider, 'chat', model)
    jid = queued(client)
    with factory() as db:
        result = handle_content_planning(db, db.get(Job, jid))
    assert calls[0].get('material_basis', {}).get('kind') == 'schematic'
    assert len(calls) == 2
    assert result['planning_suggestions']['candidates'][0]['hook'] != bad['candidates'][0]['hook']


def test_job_output_exposes_only_production_retry_request_not_private_payload():
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc)
    job = Job(id='job', tenant_id='tenant', property_id='p', user_id='admin', kind='generate_content',
        payload={'topic_id': 'space', 'plan_id': 'plan', 'private_input': 'hidden private context',
                 'credential': 'synthetic-hidden-credential'}, result={}, status='failed', progress=0,
        attempt=1, created_at=stamp, updated_at=stamp)
    output = job_dict(job)
    assert output.get('production_request') == {'kind': 'generate_content', 'topic_id': 'space', 'plan_id': 'plan'}
    assert 'hidden private context' not in json.dumps(output)
    assert 'synthetic-hidden-credential' not in json.dumps(output)
    job.kind = 'render_content'
    job.payload = {'content_version_id': 'content', 'private_input': 'hidden private context'}
    assert job_dict(job)['production_request'] == {'kind': 'render_content', 'content_version_id': 'content'}
    job.kind='platform_prepare'
    job.payload={'platform':'douyin','content_version_id':'content','session_id':'synthetic-private-session'}
    assert job_dict(job).get('preparation_request') == {'platform':'douyin','content_version_id':'content'}
    assert 'synthetic-private-session' not in json.dumps(job_dict(job))
