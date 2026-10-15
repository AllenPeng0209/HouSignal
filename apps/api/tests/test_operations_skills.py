"""Native task security uses real scoped records, jobs and local image output.

Only paid model calls are replaced. No legacy service or global profile state is
used, and no publishing endpoint is exercised.
"""
from dataclasses import replace
import hashlib
import io
import json
import subprocess
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import jobs, native_skills, provider
from app.agent_models import ModelUsage
from app.config import settings
from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.db import Base, get_db
from app.job_models import Job
from app.operations_models import ContentAccount
from app.security import Context, context_for_user, get_context


ROOT = '/api/projects/p/operations'
TEXT_SKILL = 'social-copywriting'
IMAGE_SKILL = 'native:image-editing'


@pytest.fixture
def skill_client(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "skills.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    storage = tmp_path / 'storage'
    source = storage / 't/p/image.png'
    source.parent.mkdir(parents=True)
    Image.new('RGB', (120, 80), 'red').save(source)
    with factory() as db:
        db.add_all([Tenant(id='t', name='测试团队'), Tenant(id='other', name='其他团队'),
                    User(id='u', email='editor@example.invalid', password_hash='unused', name='编辑')])
        db.flush()
        db.add_all([Membership(user_id='u', tenant_id='t', role='content'),
                    Property(id='p', tenant_id='t', name='江岸项目', city='苏州'),
                    Property(id='p2', tenant_id='t', name='另一个项目', city='苏州')])
        db.flush()
        db.add(Asset(id='image', tenant_id='t', property_id='p', name='image.png', kind='image',
                     storage_key='t/p/image.png', byte_size=source.stat().st_size,
                     sha256=hashlib.sha256(source.read_bytes()).hexdigest(), mime_type='image/png',
                     rights='authorized', metadata_json={'model_processing_authorized': False}))
        db.commit()
    ctx = Context('u', 't', 'content', '编辑', 'editor@example.invalid', None)
    current = {'ctx': ctx}
    app = FastAPI()
    app.include_router(native_skills.router)
    app.include_router(jobs.router)
    def database():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: current['ctx']
    monkeypatch.setattr(settings, 'storage_root', storage)
    monkeypatch.setattr(settings, 'app_env', 'development')
    monkeypatch.setattr(settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'synthetic-test-key')
    monkeypatch.setattr(settings, 'dashscope_chat_model', 'synthetic-text-model')
    monkeypatch.setattr(settings, 'dashscope_model', 'synthetic-text-model')
    with TestClient(app) as client:
        yield client, factory, current, ctx, source
    engine.dispose()


@pytest.fixture
def text_model(monkeypatch):
    state = {'calls': [], 'content': '江岸项目社媒草稿，待人工核验。', 'finish': 'stop',
             'message': None, 'error': None, 'during': None}
    def chat(messages, **kwargs):
        state['calls'].append({'messages': messages, **kwargs})
        if state['during']:
            state['during']()
        if state['error']:
            raise state['error']
        return {'id': 'synthetic-model-response', 'object': 'chat.completion',
                'model': 'synthetic-text-model',
                'choices': [{'index': 0, 'message': state['message'] or
                             {'role': 'assistant', 'content': state['content']},
                             'finish_reason': state['finish']}],
                'usage': {'prompt_tokens': 117, 'completion_tokens': 23, 'total_tokens': 140}}
    monkeypatch.setattr(provider, 'chat', chat)
    return state


def run(client, skill_id=TEXT_SKILL, **extra):
    return client.post(ROOT + f'/skills/{skill_id}/run', json={'input': '写一段实拍文案', **extra})


def execute(factory, jid):
    from app.worker import claim_job, execute_job
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        assert job is not None and job.id == jid
        ticket = (job.id, job.lease_token, job.attempt)
    completed = execute_job(*ticket, session_factory=factory)
    with factory() as db:
        return completed, db.get(Job, jid)


def execute_queued(client, factory, skill_id=TEXT_SKILL, **extra):
    queued = run(client, skill_id=skill_id, **extra)
    assert queued.status_code == 200, queued.text
    return execute(factory, queued.json()['job_id'])


# Losing the access-key patterns must not silently persist pasted credentials.
@pytest.mark.parametrize('secret', ['access_key=PRIVATE_ACCESS', 'access-key:PRIVATE_ACCESS', '凭证：PRIVATE_ACCESS'])
def test_redaction_retains_access_key_and_credential_assignment_protection(secret):
    assert 'PRIVATE_ACCESS' not in native_skills._public_text(secret)


@pytest.mark.parametrize('url', ['https://example.org/?auth=PRIVATE_TOKEN',
                                'https://example.org/?token=PRIVATE_TOKEN',
                                'https://example.org/?api-key=PRIVATE_TOKEN',
                                'https://example.org/?apiKey=PRIVATE_TOKEN',
                                'https://example.org/?%74oken=PRIVATE_TOKEN',
                                'https://user:PRIVATE_TOKEN@example.org/', 'javascript:alert(1)',
                                'file:///private/account.json'])
def test_public_urls_reject_access_bearing_or_unsafe_links(url):
    assert not native_skills._public_url(url)


def test_task_route_rejects_unknown_role_project_and_tenant(skill_client, text_model):
    client, factory, current, ctx, _ = skill_client
    assert run(client, skill_id='unknown').status_code == 404
    current['ctx'] = replace(ctx, role='viewer')
    assert run(client).status_code == 403
    current['ctx'] = replace(ctx, project_ids=['p2'])
    assert run(client).status_code == 404
    current['ctx'] = replace(ctx, tenant_id='other')
    assert client.get(ROOT + '/skills').status_code == 404
    with factory() as db:
        assert db.scalars(select(Job)).all() == []
    assert text_model['calls'] == []


def test_catalog_and_enqueue_are_native_without_synchronous_model_work(skill_client, text_model):
    client, factory, _, _, _ = skill_client
    listed = client.get(ROOT + '/skills')
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert len({row['skill_name'] for row in rows}) == 114
    assert all(row['execution_engine'].startswith('housignal_') and not row.get('easel_url') for row in rows)
    copy = next(row for row in rows if row['id'] == TEXT_SKILL)
    assert copy['status'] == 'ready' and copy['execution_mode'] == 'task'
    queued = run(client)
    assert queued.status_code == 200, queued.text
    with factory() as db:
        job = db.get(Job, queued.json()['job_id'])
        assert (job.kind, job.status, job.user_id, job.tenant_id, job.property_id) == ('content_skill', 'queued', 'u', 't', 'p')
    assert not text_model['calls'], 'HTTP enqueue must not make paid model calls'


def test_unconfigured_text_and_platform_navigation_do_not_enqueue(skill_client, text_model, monkeypatch):
    client, factory, _, _, _ = skill_client
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    rows = client.get(ROOT + '/skills').json()
    assert next(row for row in rows if row['id'] == TEXT_SKILL)['status'] == 'setup'
    assert run(client).status_code == 409
    platform_rows = [row for row in rows if row['engine'] == 'platform']
    assert platform_rows
    for row in platform_rows:
        assert run(client, skill_id=row['id']).status_code == 409
    with factory() as db:
        assert db.scalars(select(Job)).all() == []
    assert not text_model['calls']


def test_current_project_account_is_required_and_paused_accounts_are_rejected(skill_client, text_model):
    client, factory, _, _, _ = skill_client
    with factory() as db:
        db.add_all([ContentAccount(id='foreign', tenant_id='t', property_id='p2', platform='抖音', name='另一个账号'),
                    ContentAccount(id='foreign-tenant', tenant_id='other', property_id='p', platform='抖音', name='另一个团队'),
                    ContentAccount(id='paused', tenant_id='t', property_id='p', platform='抖音', name='已暂停', status='paused')])
        db.commit()
    for identifier, expected in [('foreign', 404), ('foreign-tenant', 404), ('missing', 404), ('paused', 409)]:
        assert run(client, account_id=identifier).status_code == expected
    assert not text_model['calls']


@pytest.mark.parametrize('payload', [{'input': ''}, {'input': '文案', 'unexpected': True},
                                    {'input': '文案', 'parameters': {'credential': 'private'}},
                                    {'input': '文案', 'asset_ids': ['image', 'image']}])
def test_invalid_task_inputs_do_not_enqueue(skill_client, text_model, payload):
    client, factory, _, _, _ = skill_client
    assert client.post(ROOT + f'/skills/{TEXT_SKILL}/run', json=payload).status_code == 422
    with factory() as db:
        assert db.scalars(select(Job)).all() == []
    assert not text_model['calls']


@pytest.mark.parametrize('change,expected', [('project', 404), ('tenant', 404), ('rights', 409), ('bytes', 409), ('link', 409)])
def test_local_processing_checks_asset_scope_rights_and_exact_file(skill_client, text_model, change, expected):
    client, factory, _, _, source = skill_client
    if change == 'bytes':
        source.write_bytes(b'not the authorized source')
    elif change == 'link':
        outside = source.parent.parent / 'private-image.png'
        outside.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(outside)
    else:
        with factory() as db:
            asset = db.get(Asset, 'image')
            if change == 'project':
                asset.property_id = 'p2'
            elif change == 'tenant':
                asset.tenant_id = 'other'
            else:
                asset.rights = 'revoked'
            db.commit()
    queued = run(client, IMAGE_SKILL, input='', asset_ids=['image'],
                 parameters={'op': 'resize', 'width': 60, 'height': 40})
    assert queued.status_code == expected, queued.text
    with factory() as db:
        assert db.scalars(select(Job)).all() == []
    assert not text_model['calls']


def test_authorized_source_must_not_follow_a_replaced_symlink(skill_client):
    _, factory, _, ctx, source = skill_client
    outside = source.parent.parent / 'another-project-image.png'
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)
    from fastapi import HTTPException
    with factory() as db, pytest.raises(HTTPException) as failure:
        native_skills._assets(db, ctx, 'p', native_skills.SkillRun(asset_ids=['image']),
                              {'accepts_assets': True, 'accepted_kinds': ['image']})
    assert failure.value.status_code == 409


def test_model_processing_permission_is_distinct_from_local_asset_rights(skill_client, text_model):
    client, factory, _, _, _ = skill_client
    queued = run(client, 'ecommerce-detail-image', asset_ids=['image'])
    assert queued.status_code == 409, queued.text
    with factory() as db:
        assert db.scalars(select(Job)).all() == []
        db.get(Asset, 'image').metadata_json = {'model_processing_authorized': True}
        db.commit()
    queued = run(client, 'ecommerce-detail-image', asset_ids=['image'])
    assert queued.status_code == 200, queued.text
    assert not text_model['calls']


def test_revoked_model_permission_after_queue_prevents_external_processing(skill_client, text_model):
    client, factory, _, _, _ = skill_client
    with factory() as db:
        db.get(Asset, 'image').metadata_json = {'model_processing_authorized': True}
        db.commit()
    queued = run(client, 'ecommerce-detail-image', asset_ids=['image'])
    assert queued.status_code == 200, queued.text
    with factory() as db:
        db.get(Asset, 'image').metadata_json = {'model_processing_authorized': False}
        db.commit()
    completed, job = execute(factory, queued.json()['job_id'])
    assert not completed and job.status == 'failed' and job.result == {}
    assert not text_model['calls']


def test_native_text_uses_verified_scoped_facts_and_redacted_account(skill_client, text_model):
    client, factory, _, _, _ = skill_client
    with factory() as db:
        db.get(Property, 'p').owner_name = 'PRIVATE_OWNER'
        db.add(ContentAccount(id='account', tenant_id='t', property_id='p', platform='抖音', name='江岸生活',
            positioning='实拍生活 13812345678', audience='本地置换家庭', voice='克制', red_lines='不承诺收益',
            homepage='https://example.org/?token=PRIVATE_TOKEN'))
        db.add_all([Fact(id='verified', tenant_id='t', property_id='p', semantic_key='area', label='面积', value='329', unit='㎡', source='测绘资料', status='verified'),
                    Fact(id='unverified', tenant_id='t', property_id='p', semantic_key='secret', label='未核验', value='PRIVATE_UNVERIFIED', source='未知', status='unverified'),
                    Fact(id='cross', tenant_id='t', property_id='p2', semantic_key='other', label='其他项目', value='PRIVATE_CROSS', source='测绘', status='verified')])
        db.commit()
    queued = run(client, account_id='account', input='写文案 联系 13812345678 sk-abcdefghijklmnop /private/material.mov token=PRIVATE_TOKEN')
    assert queued.status_code == 200, queued.text
    jid = queued.json()['job_id']
    with factory() as db:
        assert 'PRIVATE_TOKEN' not in str(db.get(Job, jid).payload)
    completed, job = execute(factory, jid)
    assert completed and job.status == 'succeeded'
    model_input = json.dumps(text_model['calls'][0]['messages'], ensure_ascii=False)
    for expected in ('江岸项目', '江岸生活', '329', '测绘资料', '克制', '写文案'):
        assert expected in model_input
    for private in ('PRIVATE_OWNER', 'PRIVATE_UNVERIFIED', 'PRIVATE_CROSS', 'PRIVATE_TOKEN', '13812345678', 'sk-abcdefghijklmnop', '/private/material.mov'):
        assert private not in model_input
    assert job.result['response'] == '江岸项目社媒草稿，待人工核验。'
    assert job.result['skill_id'] == TEXT_SKILL
    assert job.result['review_required'] is True
    assert job.result['publication_state'] == 'not_published'
    assert job.result['execution_engine'] == 'housignal_native'
    assert job.result['artifacts'] == []
    with factory() as db:
        usage = db.scalars(select(ModelUsage).where(ModelUsage.job_id == jid)).all()
        assert len(usage) == 1
        assert (usage[0].tenant_id, usage[0].property_id, usage[0].model,
                usage[0].input_tokens, usage[0].output_tokens) == ('t', 'p', 'synthetic-text-model', 117, 23)


def test_media_planning_records_each_real_model_call_once(skill_client, text_model):
    client, factory, _, _, _ = skill_client
    completed, job = execute_queued(client, factory, 'ecommerce-detail-image')
    assert completed and job.status == 'succeeded'
    assert job.result['artifacts'], 'native media planning must preserve its actual brief file'
    assert len(text_model['calls']) == 1
    with factory() as db:
        receipts = db.scalars(select(ModelUsage).where(ModelUsage.job_id == job.id)).all()
        assert len(receipts) == 1, 'the returned normalized receipt must not double-count the callback receipt'
        assert (receipts[0].model, receipts[0].input_tokens, receipts[0].output_tokens) == ('synthetic-text-model', 117, 23)


@pytest.fixture
def native_research_http(monkeypatch):
    """Keep native research and provider parsing real; replace only HTTP I/O."""
    from app import discovery
    calls = []
    def boundary(request):
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path == '/api/v1/services/aigc/text-generation/generation':
            return httpx.Response(200, json={
                'request_id': 'synthetic-search-call',
                'output': {'search_info': {'search_results': [
                    {'title': '公开住宅资料', 'url': 'https://example.org/property-research'}]}},
                'usage': {'input_tokens': 13, 'output_tokens': 8,
                          'plugins': {'search': {'count': 1}}}})
        if request.url.path == '/compatible-mode/v1/chat/completions':
            return httpx.Response(200, json={
                'id': 'synthetic-writing-call', 'object': 'chat.completion',
                'model': 'synthetic-text-model',
                'choices': [{'index': 0, 'finish_reason': 'stop',
                             'message': {'role': 'assistant', 'content': '依据公开来源生成的项目研究草稿。'}}],
                'usage': {'prompt_tokens': 117, 'completion_tokens': 23, 'total_tokens': 140}})
        return httpx.Response(404)
    transport = httpx.MockTransport(boundary)
    model_client = httpx.Client(transport=transport)
    monkeypatch.setattr(provider, '_get_client', lambda: model_client)
    monkeypatch.setattr(discovery, '_http_client', lambda: httpx.Client(transport=transport))
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    yield calls
    model_client.close()


def test_research_records_distinct_search_and_writing_calls_even_for_the_same_model(skill_client, native_research_http):
    client, factory, _, _, _ = skill_client
    completed, job = execute_queued(client, factory, 'vertical-trends', input='公开住宅趋势研究')
    assert completed and job.status == 'succeeded'
    assert [path for path, _ in native_research_http] == [
        '/api/v1/services/aigc/text-generation/generation', '/compatible-mode/v1/chat/completions']
    assert native_research_http[0][1]['parameters']['enable_search'] is True
    assert job.result['source_refs'][0]['url'] == 'https://example.org/property-research'
    with factory() as db:
        receipts = db.scalars(select(ModelUsage).where(ModelUsage.job_id == job.id)).all()
        assert len(receipts) == 2, 'a shared model name must not erase the separate paid search call'
        assert sorted((row.input_tokens, row.output_tokens) for row in receipts) == [(13, 8), (117, 23)]
        assert {row.model for row in receipts} == {'synthetic-text-model'}
        assert sum(row.input_tokens for row in receipts) == 130
        assert sum(row.output_tokens for row in receipts) == 31


def test_multi_voice_receipts_keep_each_segment_units_without_aggregate_overwrite(skill_client, text_model, monkeypatch):
    import edge_tts
    client, factory, _, _, _ = skill_client
    audio = subprocess.run([
        settings.ffmpeg_binary, '-hide_banner', '-v', 'error', '-f', 'lavfi',
        '-i', 'sine=frequency=440:duration=0.2', '-f', 'mp3', 'pipe:1'],
        capture_output=True, check=True, timeout=20).stdout
    requested = []
    class StreamedAudio:
        def __init__(self, text, **kwargs):
            self.text = text
            requested.append((text, kwargs['voice']))
        async def stream(self):
            yield {'type': 'audio', 'data': audio}
            yield {'type': 'WordBoundary', 'offset': 0, 'duration': 2_000_000, 'text': self.text}
    monkeypatch.setattr(edge_tts, 'Communicate', StreamedAudio)
    monkeypatch.setattr(settings, 'dashscope_tts_model', '')
    lines = [{'text': '你好世界啊', 'voice': 'zh-CN-XiaoxiaoNeural'},
             {'text': '这是第二段对白', 'voice': 'zh-CN-YunxiNeural'}]
    completed, job = execute_queued(client, factory, 'multi-character-dubbing', input='',
        parameters={'engine': 'edge', 'lines': json.dumps(lines, ensure_ascii=False), 'gap': 0})
    assert completed and job.status == 'succeeded', job.error
    assert requested == [('你好世界啊', 'zh-CN-XiaoxiaoNeural'), ('这是第二段对白', 'zh-CN-YunxiNeural')]
    artifact = next(row for row in job.result['artifacts'] if row['name'].endswith('.wav'))
    download = client.get(artifact['url'])
    assert download.status_code == 200
    with wave.open(io.BytesIO(download.content), 'rb') as rendered:
        assert rendered.getnframes() > 0 and rendered.getframerate() == 24000
    receipts = job.result['usage_receipts']
    assert len(receipts) == 2
    assert [row['units']['characters'] for row in receipts] == [5, 7], 'the aggregate 12 must not replace the last segment receipt'
    assert sum(row['units']['characters'] for row in receipts) == 12
    with factory() as db:
        token_rows = db.scalars(select(ModelUsage).where(ModelUsage.job_id == job.id)).all()
        assert len(token_rows) == 2
        assert all(row.input_tokens == row.output_tokens == 0 for row in token_rows)
    assert not text_model['calls'], 'ordinary Edge TTS must not invoke the paid chat model'


@pytest.mark.parametrize('message,finish', [({'content': ''}, 'stop'), ({'content': '不完整草稿'}, 'length'),
    ({'content': '调用工具', 'tool_calls': [{'function': {'name': 'publish'}}]}, 'tool_calls')])
def test_text_rejects_empty_truncated_and_tool_call_responses(skill_client, text_model, message, finish):
    client, factory, _, _, _ = skill_client
    text_model.update(message=message, finish=finish)
    completed, job = execute_queued(client, factory)
    assert not completed and job.status == 'failed'
    assert job.result == {}


@pytest.mark.parametrize('message,finish', [({'content': '尚未完成的视觉简报'}, 'length'),
    ({'content': '要求调用发布工具', 'tool_calls': [{'function': {'name': 'publish'}}]}, 'tool_calls')])
def test_media_planning_does_not_save_truncated_or_tool_call_text_as_completed_brief(skill_client, text_model, message, finish):
    client, factory, _, _, _ = skill_client
    text_model.update(message=message, finish=finish)
    completed, job = execute_queued(client, factory, 'ecommerce-detail-image')
    assert not completed and job.status == 'failed'
    assert job.result == {}


@pytest.mark.parametrize('error_type', [RuntimeError, ValueError, FileNotFoundError])
def test_provider_exception_fails_without_exposing_private_error_text(skill_client, text_model, error_type):
    client, factory, _, _, _ = skill_client
    text_model['error'] = error_type('PRIVATE_PROVIDER_ERROR token=PRIVATE_TOKEN /private/provider-secret')
    completed, job = execute_queued(client, factory)
    assert not completed and job.status == 'failed'
    assert job.result == {}
    assert 'PRIVATE_' not in job.error and '/private/' not in job.error


@pytest.mark.parametrize('change', ['cancel', 'membership', 'fact_revision', 'fact_revoke', 'account_pause', 'account_voice', 'project'])
def test_late_text_reply_is_fenced_after_cancel_permission_or_context_changes(skill_client, text_model, change):
    client, factory, _, _, _ = skill_client
    with factory() as db:
        db.add(ContentAccount(id='account', tenant_id='t', property_id='p', platform='抖音', name='江岸生活', voice='原声音'))
        db.add(Fact(id='source', tenant_id='t', property_id='p', semantic_key='area', label='面积', value='329', unit='㎡', source='测绘', status='verified'))
        db.commit()
    queued = run(client, account_id='account')
    assert queued.status_code == 200, queued.text
    jid = queued.json()['job_id']
    def changed_during_call():
        with factory() as db:
            if change == 'cancel':
                jobs.cancel_job(jid, context_for_user(db, 'u', 't'), db)
            elif change == 'membership':
                db.scalar(select(Membership).where(Membership.user_id == 'u')).active = False
            elif change == 'fact_revision':
                db.get(Fact, 'source').revision = 2
            elif change == 'fact_revoke':
                db.get(Fact, 'source').status = 'unverified'
            elif change == 'account_pause':
                db.get(ContentAccount, 'account').status = 'paused'
            elif change == 'account_voice':
                db.get(ContentAccount, 'account').voice = '另一种声音'
            else:
                db.get(Property, 'p').audience = '不同目标客群'
            db.commit()
    text_model['during'] = changed_during_call
    completed, job = execute(factory, jid)
    assert not completed
    assert job.status == ('cancelled' if change == 'cancel' else 'failed')
    assert job.result == {}


def test_forged_platform_job_is_rejected_before_model_execution(skill_client, text_model):
    client, factory, _, _, _ = skill_client
    platform = next(row for row in client.get(ROOT + '/skills').json() if row['engine'] == 'platform')
    with factory() as db:
        job = jobs.enqueue_job(db, context_for_user(db, 'u', 't'), 'p', 'content_skill',
            {'skill_id': platform['id'], 'input': '不要未经确认发帖'})
        db.commit()
        jid = job.id
    completed, job = execute(factory, jid)
    assert not completed and job.status == 'failed' and job.result == {}
    assert not text_model['calls']


def test_image_task_generates_real_scoped_artifact_and_download_checks_sha(skill_client, text_model):
    client, factory, current, ctx, source = skill_client
    completed, job = execute_queued(client, factory, IMAGE_SKILL, input='', asset_ids=['image'],
                                  parameters={'op': 'resize', 'width': 60, 'height': 40})
    assert completed and job.status == 'succeeded'
    assert job.result['publication_state'] == 'not_published'
    assert not text_model['calls'], 'a local resize must not fall back to a paid text answer'
    artifact = job.result['artifacts'][0]
    response = client.get(artifact['url'])
    assert response.status_code == 200, response.text
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'attachment;' in response.headers['content-disposition']
    assert hashlib.sha256(response.content).hexdigest() == artifact['sha256']
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (60, 40)
    assert client.get(artifact['url'].replace('/projects/p/', '/projects/p2/')).status_code == 404
    current['ctx'] = replace(ctx, tenant_id='other')
    assert client.get(artifact['url']).status_code == 404
    current['ctx'] = replace(ctx, project_ids=['p2'])
    assert client.get(artifact['url']).status_code == 404
    current['ctx'] = ctx
    directory = source.parents[2] / 't/p/skill_outputs' / job.id
    path = directory / artifact['name']
    assert path.is_file()
    assert str(source.parents[2]) not in json.dumps(job.result)
    path.write_bytes(b'changed output')
    assert client.get(artifact['url']).status_code == 409


def test_batch_processing_returns_downloadable_actual_files(skill_client, text_model):
    client, factory, _, _, _ = skill_client
    completed, job = execute_queued(client, factory, 'batch-processing', input='', asset_ids=['image'],
                                  parameters={'op': 'resize', 'width': 60, 'format': 'png'})
    assert completed and job.status == 'succeeded'
    assert job.result['artifacts'], 'successful batch output must be accessible through the project API'
    for artifact in job.result['artifacts']:
        download = client.get(artifact['url'])
        assert download.status_code == 200
        assert hashlib.sha256(download.content).hexdigest() == artifact['sha256']
    assert not text_model['calls']


@pytest.mark.parametrize('change', ['rights', 'file', 'cancel'])
def test_real_media_output_is_not_published_after_asset_change_or_cancel(skill_client, text_model, monkeypatch, change):
    from app.content_skills.tools import images
    client, factory, _, _, source = skill_client
    queued = run(client, IMAGE_SKILL, input='', asset_ids=['image'],
                 parameters={'op': 'resize', 'width': 60, 'height': 40})
    assert queued.status_code == 200, queued.text
    jid = queued.json()['job_id']
    original = images.dispatch
    generated = []
    def changed_after_local_render(*args, **kwargs):
        result = original(*args, **kwargs)
        generated.extend(result['artifact_paths'])
        if change == 'file':
            source.write_bytes(b'changed source after actual rendering')
        else:
            with factory() as db:
                if change == 'rights':
                    db.get(Asset, 'image').rights = 'revoked'
                else:
                    jobs.cancel_job(jid, context_for_user(db, 'u', 't'), db)
                db.commit()
        return result
    monkeypatch.setattr(images, 'dispatch', changed_after_local_render)
    completed, job = execute(factory, jid)
    assert generated, 'the regression must reach the real local render before revocation'
    assert not completed and job.result == {}
    assert job.status == ('cancelled' if change == 'cancel' else 'failed')
    for path in generated:
        assert client.get(ROOT + f'/skills/artifacts/{jid}/{path.name}').status_code == 404
    assert not text_model['calls']


@pytest.mark.parametrize('linked', ['file', 'directory'])
def test_output_symlink_substitution_is_rejected_even_for_matching_bytes(skill_client, text_model, linked):
    client, factory, _, _, source = skill_client
    completed, job = execute_queued(client, factory, IMAGE_SKILL, input='', asset_ids=['image'],
                                  parameters={'op': 'resize', 'width': 60, 'height': 40})
    assert completed
    artifact = job.result['artifacts'][0]
    directory = source.parents[2] / 't/p/skill_outputs' / job.id
    path = directory / artifact['name']
    if linked == 'file':
        outside = directory.parent / 'outside.png'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    else:
        outside = directory.parent / 'outside-job'
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
    response = client.get(artifact['url'])
    assert response.status_code == 404
    assert str(directory) not in response.text
