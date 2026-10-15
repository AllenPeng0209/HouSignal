"""Agent production calls use the same scoped queue contract as the workbench."""
import importlib
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.business_models import ContentVersion
from app.agent_models import ChatMessage, Conversation
from app.config import settings
from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.db import Base
from app.job_models import Job
from app.operations_models import ContentAccount, ContentPlan
from app.security import Context


@pytest.fixture
def production_env(tmp_path, monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='team', name='经纪团队'), Tenant(id='foreign', name='其他团队'),
                User(id='broker', name='经纪人', email='broker@example.test', password_hash='unused'),
                Property(id='home', tenant_id='team', name='滨江公馆', city='上海', channel='抖音',
                         marketing_confirmed=True, marketing_evidence='业主书面委托'),
                Property(id='second', tenant_id='team', name='其他楼盘', city='杭州'),
                Property(id='outside', tenant_id='foreign', name='外部楼盘', city='广州')])
    db.add(Fact(id='area', tenant_id='team', property_id='home', semantic_key='area', label='建筑面积',
                value='328', unit='㎡', source='测绘报告第2页', status='verified', revision=1, required=True))
    db.add(ContentAccount(id='account', tenant_id='team', property_id='home', name='经纪人账号', platform='小红书'))
    db.add(Membership(id='membership', user_id='broker', tenant_id='team', role='content', project_ids=['home']))
    db.add(ContentPlan(id='plan', tenant_id='team', property_id='home', title='江景生活', channel='小红书',
                       topic_id='space', account_id='account'))
    db.add(ContentVersion(id='draft', tenant_id='team', property_id='home', topic='江景生活', script='从客厅看江景',
                          fact_refs=[{'id': 'area', 'revision': 1}], storyboard=[], created_by='broker'))
    db.commit()
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'configured-for-preflight-only')
    monkeypatch.setattr(settings, 'native_text_backend', 'dashscope')
    yield db, Context('broker', 'team', 'content', '经纪人', 'broker@example.test', ['home'])
    db.close()
    engine.dispose()


def run(env, name, args=None, *, raw_text='请帮我生成制作需要的内容', pid='home', ctx=None):
    module = importlib.import_module('app.production_tools')
    db, default = env
    return module.execute_production_tool(db, ctx or default, pid, name, args or {}, raw_text=raw_text)


def test_generation_enqueues_native_contract_uses_plan_channel_and_rolls_back(production_env):
    db, _ = production_env
    result = run(production_env, 'generate_content', {'topic_id': 'space', 'plan_id': 'plan', 'channel': '抖音'})
    job = db.get(Job, result['data']['job_id'])
    assert (job.kind, job.user_id, job.property_id, job.status) == ('generate_content', 'broker', 'home', 'queued')
    assert job.payload == {'topic_id': 'space', 'plan_id': 'plan', 'channel': '小红书'}
    assert result['data']['completed'] is False
    assert any(source['kind'] == 'job' and source['id'] == job.id for source in result['sources'])
    db.rollback()
    assert db.get(Job, result['data']['job_id']) is None


def test_generation_rechecks_fact_gate_and_invalid_topic_before_enqueue(production_env):
    db, _ = production_env
    db.get(Fact, 'area').status = 'unverified'
    with pytest.raises(HTTPException) as error:
        run(production_env, 'generate_content', {'topic_id': 'space'})
    assert error.value.status_code == 409
    assert db.scalar(select(Job)) is None


def test_planning_enqueues_full_validated_native_request(production_env):
    db, _ = production_env
    result = run(production_env, 'plan_content', {'subject': '彭先生喜欢的江景', 'topic_id': 'space',
                 'channel': '视频号', 'target_seconds': 45, 'skill_ids': ['topic-matrix']})
    job = db.get(Job, result['data']['job_id'])
    assert job.kind == 'content_planning'
    assert job.payload == {'content_type': 'professional', 'subject': '彭先生喜欢的江景', 'topic_id': 'space',
                          'account_id': None, 'channel': '微信视频号', 'reference_id': None,
                          'skill_ids': ['topic-matrix'], 'intent': '', 'target_seconds': 45}


def test_planning_checks_account_project_and_channel(production_env):
    db, _ = production_env
    with pytest.raises(HTTPException) as error:
        run(production_env, 'plan_content', {'account_id': 'account', 'channel': '抖音'})
    assert error.value.status_code == 409
    assert db.scalar(select(Job)) is None


def test_skill_uses_actual_parameter_defaults_and_job_kind(production_env):
    db, _ = production_env
    result = run(production_env, 'run_production_skill', {'skill_id': 'roi-analysis',
                 'parameters': {'records': [{'ad_spend': 100, 'conversions': 2, 'avg_order_value': 90}]}})
    job = db.get(Job, result['data']['job_id'])
    assert job.kind == 'content_skill'
    assert (job.payload['skill_id'], job.payload['skill_name']) == ('roi-analysis', 'roi-calculator')
    assert job.payload['parameters']['action'] == 'read'
    assert job.payload['parameters']['records'] == [{'ad_spend': 100, 'conversions': 2, 'avg_order_value': 90}]
    assert job.payload['asset_ids'] == [] and job.payload['account_id'] is None


def test_skill_rejects_unknown_params_and_missing_required_assets(production_env):
    db, _ = production_env
    with pytest.raises(HTTPException) as error:
        run(production_env, 'run_production_skill', {'skill_id': 'roi-analysis', 'parameters': {'shell': 'echo fake'}})
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        run(production_env, 'run_production_skill', {'skill_id': 'image-enhancement'})
    assert error.value.status_code == 422
    assert db.scalar(select(Job)) is None


def test_skill_preflight_exposes_setup_without_queueing(production_env, monkeypatch):
    db, _ = production_env
    monkeypatch.setattr(settings, 'dashscope_video_model', '')
    monkeypatch.setattr(settings, 'native_video_backend', 'dashscope')
    result = run(production_env, 'run_production_skill', {'skill_id': 'ai-video', 'input': '江景客厅动态短片'})
    assert result['data']['status'] == 'setup'
    assert result['data']['job_id'] is None
    assert result['data']['requirements']
    assert db.scalar(select(Job)) is None


def test_paid_media_needs_specific_budget_authorization_in_original_user_message(production_env, monkeypatch):
    db, _ = production_env
    monkeypatch.setattr(settings, 'dashscope_image_model', 'wan2.6-t2i')
    args = {'skill_id': 'ai-image', 'input': '江景客厅', 'confirmed_budget': True}
    result = run(production_env, 'run_production_skill', args, raw_text='帮我生成一张江景图片')
    assert result['data']['status'] == 'needs_confirmation'
    assert result['data']['job_id'] is None and db.scalar(select(Job)) is None
    result = run(production_env, 'run_production_skill', args, raw_text='允许付费生成这张江景图片，按团队预算执行')
    job = db.get(Job, result['data']['job_id'])
    assert job.kind == 'content_skill'
    assert 'confirmed_budget' not in job.payload and 'confirmed_budget' not in job.payload['parameters']


@pytest.mark.parametrize(('skill_id', 'parameters'), [
    ('speech-to-text', {'engine': 'cloud', 'format': 'txt'}),
    ('video-to-article', {'engine': 'cloud', 'op': 'transcribe'}),
    ('paper-explanation', {'format': 'video', 'width': 1080, 'height': 1920}),
    ('paper-explanation', {'format': 'both', 'width': 1080, 'height': 1920}),
])
def test_paid_suboperations_cannot_bypass_budget_confirmation(production_env, monkeypatch, skill_id, parameters):
    db, _ = production_env
    monkeypatch.setattr(settings, 'dashscope_tts_model', 'qwen3-tts-flash')
    source = settings.storage_root / 'source.txt'
    source.write_text('用户已提供的论文原文或语音输入', encoding='utf-8')
    db.add(Asset(id='source', tenant_id='team', property_id='home', name='source.txt',
                 kind='document' if skill_id == 'paper-explanation' else 'audio',
                 mime_type='text/plain', storage_key='source.txt', byte_size=source.stat().st_size,
                 sha256=hashlib.sha256(source.read_bytes()).hexdigest(), rights='authorized',
                 rights_evidence='用户明确授权模型处理', metadata_json={'model_processing_authorized': True}))
    db.commit()
    result = run(production_env, 'run_production_skill', {'skill_id': skill_id, 'parameters': parameters, 'asset_ids': ['source']},
                 raw_text='帮我处理这份视频或论文，生成实际产物')
    assert result['data']['status'] == 'needs_confirmation'
    assert result['data']['job_id'] is None
    assert db.scalar(select(Job)) is None


def test_free_edge_multivoice_remains_available_without_paid_media_confirmation(production_env, monkeypatch):
    db, _ = production_env
    monkeypatch.setattr(settings, 'dashscope_tts_model', '')
    result = run(production_env, 'run_production_skill', {'skill_id': 'multi-character-dubbing',
                 'input': '经纪人：欢迎来看这套江景房。', 'parameters': {'engine': 'edge'}},
                 raw_text='帮我做普通多角色配音')
    assert db.get(Job, result['data']['job_id']).kind == 'content_skill'


def test_render_reuses_native_readiness_and_does_not_queue_unready_content(production_env):
    db, _ = production_env
    with pytest.raises(HTTPException) as error:
        run(production_env, 'render_content', {'content_version_id': 'draft'}, raw_text='渲染这个版本的视频')
    assert error.value.status_code == 409
    assert db.scalar(select(Job)) is None


def test_render_enqueues_real_authorized_storyboard_and_stays_transactional(production_env):
    db, _ = production_env
    db.add(Asset(id='shot', tenant_id='team', property_id='home', name='客厅实拍.jpg', kind='image',
                 mime_type='image/jpeg', storage_key='team/home/shot.jpg', sha256='a' * 64, byte_size=10,
                 rights='authorized', rights_evidence='业主授权拍摄', origin_type='actual'))
    db.get(ContentVersion, 'draft').storyboard = [{'asset_id': 'shot', 'duration': 15, 'caption': '江景客厅'}]
    db.commit()
    result = run(production_env, 'render_content', {'content_version_id': 'draft'}, raw_text='渲染这个版本的视频')
    job = db.get(Job, result['data']['job_id'])
    assert job.kind == 'render_content' and job.payload == {'content_version_id': 'draft'}
    db.rollback()
    assert db.get(Job, result['data']['job_id']) is None


def test_capabilities_read_real_native_schema_including_unavailable_state(production_env, monkeypatch):
    monkeypatch.setattr(settings, 'dashscope_video_model', '')
    monkeypatch.setattr(settings, 'native_video_backend', 'dashscope')
    result = run(production_env, 'get_production_capabilities', {'skill_id': 'ai-video'})
    skill = result['data']['skills'][0]
    assert skill['id'] == 'ai-video' and skill['status'] == 'setup'
    assert any(field['key'] == 'duration' for field in skill['input_schema']['parameters'])


def test_capability_discovery_matches_chinese_product_title(production_env):
    result = run(production_env, 'get_production_capabilities', {'query': '多角色配音'})
    assert any(skill['id'] == 'multi-character-dubbing' for skill in result['data']['skills'])


def test_job_result_preserves_actual_artifact_links_and_redacts_sensitive_fields(production_env):
    db, _ = production_env
    db.add(Job(id='media-job', tenant_id='team', property_id='home', user_id='broker', kind='content_skill',
               status='succeeded', payload={'input': 'private payload'}, result={
                   'response': '实际处理完成', 'api_key': 'hidden-key', 'storage_key': '/private/original.mp4',
                   'artifacts': [{'name': 'result.mp4', 'url': '/api/projects/home/operations/skills/artifacts/media-job/result.mp4',
                                  'byte_size': 2345, 'sha256': 'a' * 64}],
                   'metadata': {'token': 'hidden-token', 'message': 'api_key=hidden-text'}}))
    db.commit()
    result = run(production_env, 'get_production_job', {'job_id': 'media-job'})
    job = result['data']['job']
    assert job['status'] == 'succeeded'
    assert job['result']['artifacts'][0]['url'].endswith('/media-job/result.mp4')
    assert 'hidden-key' not in str(result) and 'hidden-token' not in str(result) and 'hidden-text' not in str(result)
    assert 'private payload' not in str(result) and '/private/original.mp4' not in str(result)


@pytest.mark.parametrize(('name', 'args'), [
    ('get_production_job', {'job_id': 'wrong-project'}),
    ('get_production_job', {'job_id': 'private-chat'}),
    ('cancel_production_job', {'job_id': 'wrong-project'}),
])
def test_job_lookup_never_leaks_other_project_or_chat(production_env, name, args):
    db, _ = production_env
    db.add_all([Job(id='wrong-project', tenant_id='team', property_id='second', user_id='broker', kind='content_skill'),
                Job(id='private-chat', tenant_id='team', property_id='home', user_id='someone', kind='chat')])
    db.commit()
    with pytest.raises(HTTPException) as error:
        run(production_env, name, args, raw_text='取消这个任务')
    assert error.value.status_code == 404


def test_cancel_requires_job_owner_and_clears_lease_without_commit(production_env):
    db, ctx = production_env
    db.add_all([Job(id='own', tenant_id='team', property_id='home', user_id='broker', kind='content_skill',
                    status='running', lease_token='lease', result={'partial': 'old'}),
                Job(id='someone', tenant_id='team', property_id='home', user_id='other', kind='content_skill')])
    db.commit()
    with pytest.raises(HTTPException) as error:
        run(production_env, 'cancel_production_job', {'job_id': 'someone'}, raw_text='取消这个任务')
    assert error.value.status_code == 403
    result = run(production_env, 'cancel_production_job', {'job_id': 'own'}, raw_text='取消这个任务')
    assert result['data']['job']['status'] == 'cancelled'
    assert db.get(Job, 'own').lease_token is None and db.get(Job, 'own').result == {}
    db.rollback()
    assert db.get(Job, 'own').status == 'running'


def test_project_and_role_scope_guard_all_mutations(production_env):
    db, ctx = production_env
    viewer = Context(ctx.user_id, ctx.tenant_id, 'viewer', ctx.name, ctx.email, ctx.project_ids)
    with pytest.raises(HTTPException) as error:
        run(production_env, 'generate_content', {'topic_id': 'space'}, ctx=viewer)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        run(production_env, 'get_production_capabilities', pid='second')
    assert error.value.status_code == 404
    with pytest.raises((HTTPException, ValueError)):
        run(production_env, 'generate_content', {'topic_id': 'space'}, raw_text='')
    assert db.scalar(select(Job)) is None


@pytest.mark.parametrize(('name', 'args'), [
    ('generate_content', {'topic_id': 'bogus'}),
    ('plan_content', {'target_seconds': True}),
    ('plan_content', {'skill_ids': ['imaginary']}),
    ('run_production_skill', {'skill_id': 'unknown'}),
    ('generate_content', {'topic_id': 'space', 'tenant_id': 'foreign'}),
    ('list_production_jobs', {'limit': True}),
    ('get_production_job', {}),
])
def test_malformed_tools_fail_before_any_job(production_env, name, args):
    db, _ = production_env
    with pytest.raises((ValueError, HTTPException)):
        run(production_env, name, args)
    assert db.scalar(select(Job)) is None


def test_lists_are_project_scoped_and_exclude_chat_and_external_operations(production_env):
    db, _ = production_env
    db.add_all([Job(id='native', tenant_id='team', property_id='home', user_id='broker', kind='content_skill'),
                Job(id='foreign-job', tenant_id='team', property_id='second', user_id='broker', kind='content_skill'),
                Job(id='publish', tenant_id='team', property_id='home', user_id='broker', kind='platform_publish')])
    db.commit()
    jobs = run(production_env, 'list_production_jobs')['data']['jobs']
    assert [item['id'] for item in jobs] == ['native']
    content = run(production_env, 'list_project_content')['data']['contents']
    assert [item['id'] for item in content] == ['draft']


def test_bridge_never_dispatches_publication_or_external_notification(production_env):
    with pytest.raises((ValueError, HTTPException)):
        run(production_env, 'run_production_skill', {'skill_id': 'douyin-publish'})


@pytest.mark.parametrize('raw_text', ['现在这个项目怎么样', '如何生成这个版本的视频？', '不要渲染这个视频'])
def test_render_never_enqueues_from_question_or_negative_user_request(production_env, raw_text):
    db, _ = production_env
    # A ready version is necessary: rejection must be about user intent.
    db.add(Asset(id='shot', tenant_id='team', property_id='home', name='实拍.jpg', kind='image',
                 mime_type='image/jpeg', storage_key='shot.jpg', sha256='a' * 64, byte_size=10,
                 rights='authorized', rights_evidence='业主授权拍摄', origin_type='actual'))
    db.get(ContentVersion, 'draft').storyboard = [{'asset_id': 'shot', 'duration': 15, 'caption': '江景客厅'}]
    db.commit()
    with pytest.raises(HTTPException) as error:
        run(production_env, 'render_content', {'content_version_id': 'draft'}, raw_text=raw_text)
    assert error.value.status_code == 409
    assert db.scalar(select(Job)) is None


def test_result_redaction_normalizes_credential_header_keys(production_env):
    db, _ = production_env
    db.add(Job(id='sensitive', tenant_id='team', property_id='home', user_id='broker', kind='content_skill',
               status='failed', result={'response': '失败', 'apiKey': 'hidden-camel-key',
                   'headers': {'X-API-Key': 'hidden-header-key'}, 'localPath': '/private/export.mov',
                   'download_url': 'https://storage.example/file?X-Amz-Signature=sensitive-signature'}))
    db.commit()
    result = run(production_env, 'get_production_job', {'job_id': 'sensitive'})
    assert 'hidden-camel-key' not in str(result) and 'hidden-header-key' not in str(result)
    assert '/private/export.mov' not in str(result) and 'sensitive-signature' not in str(result)


def test_machine_generated_identifiers_and_artifact_urls_survive_phone_redaction(production_env, monkeypatch):
    from uuid import UUID
    from app import job_models
    db, _ = production_env
    identity = '00000000-0000-4000-8000-010123456789'
    monkeypatch.setattr(job_models, 'uuid4', lambda: UUID(identity))
    result = run(production_env, 'run_production_skill', {'skill_id': 'roi-analysis'})
    assert result['data']['job_id'] == identity
    assert db.get(Job, result['data']['job_id']) is not None
    job = db.get(Job, identity)
    job.status = 'succeeded'
    url = f'/api/projects/home/operations/skills/artifacts/{identity}/result.txt'
    job.result = {'response': '已完成，客户电话13800138000不应显示',
                  'artifacts': [{'name': 'result.txt', 'url': url, 'byte_size': 2, 'sha256': 'a' * 64}]}
    result = run(production_env, 'get_production_job', {'job_id': identity})
    assert result['data']['job']['id'] == identity
    assert result['data']['job']['result']['artifacts'][0]['url'] == url
    assert '13800138000' not in str(result)


def test_enqueued_project_skill_runs_native_worker_and_yields_real_downloadable_artifact(production_env):
    db, ctx = production_env
    from app.native_skills import handle_content_skill
    result = run(production_env, 'run_production_skill', {'skill_id': 'roi-analysis',
                 'parameters': {'records': [{'ad_spend': 100, 'conversions': 2, 'avg_order_value': 90}]}})
    job = db.get(Job, result['data']['job_id'])
    actual = handle_content_skill(db, job)
    job.result = actual
    job.status = 'succeeded'
    db.flush()
    result = run(production_env, 'get_production_job', {'job_id': job.id})
    artifacts = result['data']['job']['result']['artifacts']
    assert artifacts and all(item['byte_size'] > 0 for item in artifacts)
    assert all(item['url'].startswith(f'/api/projects/home/operations/skills/artifacts/{job.id}/') for item in artifacts)
    assert '80.0' in result['data']['job']['result']['response']


def workflow_parent(db):
    db.add(Conversation(id='conversation', tenant_id='team', property_id='home', user_id='broker', title='视频制作'))
    db.add_all([Job(id='parent', tenant_id='team', property_id='home', user_id='broker', kind='chat',
                    status='running', payload={'conversation_id': 'conversation', 'workflow_depth': 1}),
                Job(id='upstream', tenant_id='team', property_id='home', user_id='broker', kind='content_skill',
                    status='queued', payload={'skill_id': 'ai-video'})])
    db.commit()
    db.info.update(agent_conversation_id='conversation', agent_job_id='parent')


def test_continuation_creates_private_dependency_bound_chat_and_deduplicates(production_env):
    db, _ = production_env
    from app.security import decrypt_pii
    workflow_parent(db)
    args = {'job_id': 'upstream', 'instruction': '生成视频后继续配音，再整理实际产物'}
    raw = '给我生成江景视频，然后继续配音。客户电话13800138000。'
    result = run(production_env, 'continue_after_production', args, raw_text=raw)
    continuation = db.get(Job, result['data']['job_id'])
    assert continuation.kind == 'chat' and continuation.status == 'queued'
    assert continuation.user_id == 'broker' and continuation.property_id == 'home'
    assert continuation.payload['conversation_id'] == 'conversation'
    assert continuation.payload['depends_on_job_id'] == 'upstream'
    assert continuation.payload['workflow_depth'] == 2
    assert decrypt_pii(continuation.payload['workflow_authorization_cipher']) == raw
    messages = db.scalars(select(ChatMessage).where(ChatMessage.job_id == continuation.id)).all()
    assert len(messages) == 2 and {message.role for message in messages} == {'user', 'assistant'}
    assert all('13800138000' not in message.content for message in messages)
    assert any(source['kind'] == 'job' and source['id'] == continuation.id and source['workflow'] is True
               for source in result['sources'])
    repeated = run(production_env, 'continue_after_production', args, raw_text=raw)
    assert repeated['data']['job_id'] == continuation.id
    assert len(db.scalars(select(ChatMessage).where(ChatMessage.job_id == continuation.id)).all()) == 2
    continuation_id = continuation.id
    db.rollback()
    assert db.get(Job, continuation_id) is None


@pytest.mark.parametrize('override', [{}, {'agent_conversation_id': 'missing', 'agent_job_id': 'parent'},
                                     {'agent_conversation_id': 'conversation', 'agent_job_id': 'wrong-parent'}])
def test_continuation_needs_server_bound_active_private_conversation(production_env, override):
    db, _ = production_env
    workflow_parent(db)
    db.info.pop('agent_job_id', None)
    db.info.pop('agent_conversation_id', None)
    db.info.update(override)
    with pytest.raises((HTTPException, ValueError)):
        run(production_env, 'continue_after_production', {'job_id': 'upstream', 'instruction': '继续配音'},
            raw_text='生成好后继续配音')
    assert db.scalar(select(Job).where(Job.payload['depends_on_job_id'].as_string() == 'upstream')) is None


def test_continuation_rejects_model_fabricated_workflow(production_env):
    db, _ = production_env
    workflow_parent(db)
    with pytest.raises(HTTPException) as error:
        run(production_env, 'continue_after_production', {'job_id': 'upstream', 'instruction': '继续做视频'},
            raw_text='给我生成一张图片')
    assert error.value.status_code == 409


@pytest.mark.parametrize('depth', [5, 12, 1000])
def test_continuation_accepts_authorized_workflows_beyond_five_steps(production_env, depth):
    db, _ = production_env
    workflow_parent(db)
    db.get(Job, 'parent').payload = {'conversation_id': 'conversation', 'workflow_depth': depth}
    result = run(production_env, 'continue_after_production',
                 {'job_id': 'upstream', 'instruction': '继续核验制作结果'}, raw_text='生成后继续核验结果')
    continuation = db.get(Job, result['data']['job_id'])
    assert continuation.payload['workflow_depth'] == depth + 1
    assert continuation.payload['workflow_parent_job_id'] == 'parent'
    assert continuation.payload['depends_on_job_id'] == 'upstream'


@pytest.mark.parametrize('depth', [-1, 1.5, '5', None, True])
def test_continuation_rejects_invalid_workflow_depth(production_env, depth):
    db, _ = production_env
    workflow_parent(db)
    parent = db.get(Job, 'parent')
    parent.payload = {'conversation_id': 'conversation', 'workflow_depth': depth}
    # JSON boolean True compares equal to the original integer 1 in Python;
    # force this malformed persisted value through the actual database boundary.
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(parent, 'payload')
    with pytest.raises(HTTPException) as error:
        run(production_env, 'continue_after_production',
            {'job_id': 'upstream', 'instruction': '继续核验制作结果'}, raw_text='生成后继续核验结果')
    assert error.value.status_code == 409
    assert db.scalar(select(Job).where(Job.payload['depends_on_job_id'].as_string() == 'upstream')) is None


def test_continuation_preserves_failed_upstream_for_real_failure_report(production_env):
    db, _ = production_env
    workflow_parent(db)
    db.get(Job, 'upstream').status = 'failed'
    db.get(Job, 'upstream').error = '真实服务错误'
    result = run(production_env, 'continue_after_production', {'job_id': 'upstream', 'instruction': '生成后继续配音'},
                 raw_text='生成后继续配音')
    user = db.scalar(select(ChatMessage).where(ChatMessage.job_id == result['data']['job_id'], ChatMessage.role == 'user'))
    assert 'get_production_job' in user.content and '失败' in user.content and '停止' in user.content
    assert db.get(Job, 'upstream').error == '真实服务错误'


def test_continuation_cannot_fan_out_by_varying_instruction_punctuation(production_env):
    db, _ = production_env
    workflow_parent(db)
    first = run(production_env, 'continue_after_production', {'job_id': 'upstream', 'instruction': '继续配音。'},
                raw_text='生成后继续配音')
    second = run(production_env, 'continue_after_production', {'job_id': 'upstream', 'instruction': '继续配音！然后再合成'},
                 raw_text='生成后继续配音')
    assert first['data']['job_id'] == second['data']['job_id']
    children = db.scalars(select(Job).where(Job.payload['depends_on_job_id'].as_string() == 'upstream')).all()
    assert len(children) == 1
    assert children[0].payload['workflow_instruction'] == '继续配音。'

