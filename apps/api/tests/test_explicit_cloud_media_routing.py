"""Explicit cloud audio/vision keep their own guarded, durable billing lane.

The model and audio-codec boundaries are controlled; SQLite jobs, worker leases,
authorization and the canonical independent usage recorder are real. No network
client, resident model, Whisper model or paid generation is opened in this suite.
"""
import hashlib
import io
from pathlib import Path
import wave

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app import jobs, media, native_skills, provider
from app.agent_models import ModelUsage
from app.config import settings
from app.content_skills import media as native_media
from app.content_skills.tools import speech
from app.core_models import Asset, Membership
from app.job_models import Job
from app.security import context_for_user
from test_operations_skills import execute, run, skill_client


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    monkeypatch.setattr(provider, '_get_client', lambda: pytest.fail('No real model HTTP is permitted'))


def response(model='cloud-asr-fixture'):
    return {'model': model, 'choices': [{'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': '这是已授权素材的受控转录。'}}],
            'usage': {'prompt_tokens': 17, 'completion_tokens': 9}}


def test_explicit_speech_model_uses_guarded_cloud_callback_and_not_native_text():
    seen = []
    def cloud(messages, **kwargs):
        seen.append(kwargs)
        return response()
    context = {'cloud_chat': cloud,
               'chat': lambda *args, **kwargs: pytest.fail('ASR reached resident text'),
               'before_model_call': lambda: pytest.fail('wrapped callback owns its budget lane')}
    result = speech._chat([{'role': 'user', 'content': 'audio proxy'}], context, model='qwen3-asr-flash')
    assert result['model'] == 'cloud-asr-fixture'
    assert seen == [{'model': 'qwen3-asr-flash', 'max_tokens': 2200}]


def test_plain_article_text_stays_on_guarded_native_text_callback():
    seen = []
    def native(messages, **kwargs):
        seen.append(kwargs)
        return response('ppu:text-fixture')
    result = speech._chat([{'role': 'user', 'content': '整理已有转录'}],
                         {'chat': native, 'chat_manages_lifecycle': True, 'cloud_chat': lambda *a, **k: pytest.fail('article text went cloud'),
                          'before_model_call': lambda: pytest.fail('native callback owns its budget lane')})
    assert result['model'] == 'ppu:text-fixture'
    assert seen == [{'model': None, 'max_tokens': 2200}]


def test_fixed_pure_text_model_preserves_project_chat_and_managed_metering():
    seen = []
    def text(messages, **kwargs):
        seen.append(kwargs)
        return response('cloud-writing-fixture')
    result = speech._chat([{'role': 'user', 'content': '生成文章规划'}],
                         {'chat': text, 'chat_manages_lifecycle': True, 'cloud_chat': lambda *a, **k: pytest.fail('text was misclassified as ASR'),
                          'before_model_call': lambda: pytest.fail('managed text wrapper owns its gate'),
                          'begin_usage_call': lambda: pytest.fail('managed text wrapper owns its UUID'),
                          'on_usage': lambda receipt: pytest.fail('managed text wrapper owns its receipt')},
                         model='cloud-writing-fixture')
    assert result['model'] == 'cloud-writing-fixture'
    assert seen == [{'model': 'cloud-writing-fixture', 'max_tokens': 2200}]


@pytest.mark.parametrize('part', [
    {'type': 'input_audio', 'input_audio': {'data': 'data:audio/wav;base64,controlled'}},
    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,controlled'}},
])
def test_explicit_multimodal_payload_requires_cloud_wrapper_even_with_nondefault_model(part):
    seen = []
    def cloud(messages, **kwargs):
        seen.append(kwargs)
        return response('explicit-media-fixture')
    messages = [{'role': 'user', 'content': [part]}]
    context = {'chat': lambda *a, **k: pytest.fail('multimodal input reached native text'), 'cloud_chat': cloud}
    assert speech._chat(messages, context, model='explicit-media-fixture')['model'] == 'explicit-media-fixture'
    assert seen == [{'model': 'explicit-media-fixture', 'max_tokens': 2200}]
    with pytest.raises(ValueError, match='cloud_chat|云'):
        speech._chat(messages, {'chat': context['chat']}, model='explicit-media-fixture')


def test_project_context_cannot_fallback_around_missing_guarded_cloud_callback(monkeypatch):
    monkeypatch.setattr(provider, 'cloud_chat', lambda *a, **k: pytest.fail('unguarded paid fallback'))
    with pytest.raises(ValueError, match='cloud_chat|云'):
        speech._chat([], {'chat': lambda *a, **k: pytest.fail('ASR sent to text backend')}, model='qwen3-asr-flash')


@pytest.mark.parametrize('setting,value', [
    ('dashscope_api_key', ''), ('dashscope_api_key', ' '),
    ('dashscope_asr_model', ''), ('dashscope_asr_model', ' '),
    ('dashscope_base_url', 'http://localhost:9999'),
    ('dashscope_base_url', 'https://dashscope.aliyuncs.com:broken/v1'),
])
def test_explicit_cloud_readiness_rejects_missing_or_invalid_configuration(monkeypatch, setting, value):
    monkeypatch.setattr(settings, 'native_text_backend', 'resident_ppu')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'synthetic-test-key')
    monkeypatch.setattr(settings, 'dashscope_asr_model', 'qwen3-asr-flash')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setattr(settings, setting, value)
    assert native_media.readiness_for('video-to-article', {'engine': 'cloud', 'op': 'transcribe'})['status'] == 'setup'


def test_local_whisper_transcribe_does_not_require_a_cloud_key(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'http://invalid-cloud')
    monkeypatch.setattr(speech, 'local_asr_ready', lambda: True)
    monkeypatch.setattr(native_media.shutil, 'which', lambda binary: '/controlled/codec')
    monkeypatch.setattr(speech, 'timed_transcript', lambda *a, **k: [{'start': 0, 'end': 1, 'text': '本地转录'}])
    monkeypatch.setattr(provider, 'cloud_chat', lambda *a, **k: pytest.fail('local ASR made a paid call'))
    assert native_media.readiness_for('video-to-article', {'engine': 'local', 'op': 'transcribe'})['status'] == 'ready'
    assert speech.transcript({'path': str(tmp_path / 'source.wav')}, {}, tmp_path, {'engine': 'local'}) == ('本地转录', {})


@pytest.fixture
def resident_cloud(skill_client, monkeypatch):
    from app.content_skills.tools import resident_text
    monkeypatch.setattr(settings, 'native_text_backend', 'resident_ppu')
    monkeypatch.setattr(settings, 'native_local_text_daily_token_budget', 1000)
    monkeypatch.setattr(settings, 'development_token_budget', 1000)
    monkeypatch.setattr(settings, 'dashscope_asr_model', 'qwen3-asr-flash')
    monkeypatch.setattr(settings, 'dashscope_vision_model', 'qwen-vl-plus')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setattr(resident_text, 'readiness', lambda: {'status': 'ready', 'reason': 'controlled resident fixture'})
    monkeypatch.setattr(provider, 'chat', lambda *a, **k: pytest.fail('explicit ASR/OCR reached native text'))
    monkeypatch.setattr(speech, '_probe', lambda *a, **k: 1.0)
    monkeypatch.setattr(media, '_probe', lambda *a, **k: ({'streams': [{'codec_type': 'audio'}]}, 1.0))
    def codec(command, *args, **kwargs):
        Path(command[-1]).write_bytes(b'controlled-mono-proxy')
        return b''
    monkeypatch.setattr(speech, '_run', codec)
    monkeypatch.setattr(media, '_run', codec)
    state = {'calls': [], 'during': None, 'error': None, 'omit_model': False}
    def cloud(messages, **kwargs):
        state['calls'].append({'messages': messages, **kwargs})
        if state['during']:
            state['during']()
        if state['error']:
            raise state['error']
        value = response('cloud-asr-fixture' if 'asr' in kwargs['model'] else 'cloud-vision-fixture')
        if state['omit_model']:
            value.pop('model')
        return value
    monkeypatch.setattr(provider, 'cloud_chat', cloud)
    return state


def add_audio(factory):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(16000)
        sound.writeframes(b'\x01\x00' * 16000)
    source = settings.storage_root / 't/p/source.wav'
    source.write_bytes(buffer.getvalue())
    with factory() as db:
        db.add(Asset(id='audio', tenant_id='t', property_id='p', name='source.wav', kind='audio',
                     storage_key='t/p/source.wav', mime_type='audio/wav', rights='authorized',
                     rights_evidence='受控验收素材授权', metadata_json={'model_processing_authorized': True},
                     byte_size=source.stat().st_size, sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
        db.commit()
    return 'audio'


def authorize_image(factory):
    with factory() as db:
        asset = db.get(Asset, 'image')
        asset.metadata_json = {'model_processing_authorized': True}
        asset.rights_evidence = '受控验收素材授权'
        db.commit()
    return 'image'


def usage_rows(factory, jid):
    with factory() as db:
        return db.scalars(select(ModelUsage).where(ModelUsage.job_id == jid)).all()


def assert_one_cloud_receipt(factory, jid, model='cloud-asr-fixture'):
    rows = usage_rows(factory, jid)
    assert len(rows) == 1, 'each consumed call survives rollback exactly once'
    assert (rows[0].tenant_id, rows[0].property_id, rows[0].model,
            rows[0].input_tokens, rows[0].output_tokens) == ('t', 'p', model, 17, 9)


def queue_native(client, factory):
    identifier = add_audio(factory)
    queued = run(client, 'video-to-article', input='', asset_ids=[identifier],
                 parameters={'op': 'transcribe', 'engine': 'cloud'})
    assert queued.status_code == 200, queued.text
    return queued.json()['job_id']


def queue_extract(factory, identifier):
    with factory() as db:
        job = jobs.enqueue_job(db, context_for_user(db, 'u', 't'), 'p', 'extract_asset', {'asset_id': identifier})
        db.commit()
        return job.id


def execute_extract(factory, jid):
    from app.worker import claim_job, execute_job
    with factory() as db:
        job = claim_job(db, kinds=('extract_asset',))
        assert job and job.id == jid
        ticket = (job.id, job.lease_token, job.attempt)
    completed = execute_job(*ticket, session_factory=factory)
    with factory() as db:
        return completed, db.get(Job, jid)


def test_native_cloud_asr_has_separate_budget_and_one_canonical_receipt(skill_client, resident_cloud):
    client, factory, *_ = skill_client
    with factory() as db:
        db.add(ModelUsage(tenant_id='t', property_id='p', job_id='earlier-local', model='ppu:fixture', input_tokens=1500, output_tokens=0))
        db.commit()
    jid = queue_native(client, factory)
    completed, job = execute(factory, jid)
    assert completed and job.status == 'succeeded', job.error
    assert len(resident_cloud['calls']) == 1
    assert resident_cloud['calls'][0]['model'] == 'qwen3-asr-flash'
    assert_one_cloud_receipt(factory, jid)
    assert len(job.result['usage_receipts']) == 1


@pytest.mark.parametrize('lane', ['native', 'extract-audio', 'extract-image'])
@pytest.mark.parametrize('failure', ['late-grant-revoke', 'late-membership-revoke', 'provider-usage-error'])
def test_actual_worker_retains_consumed_cloud_usage_when_business_rolls_back(skill_client, resident_cloud, lane, failure):
    client, factory, *_ = skill_client
    identifier = 'audio'
    if lane == 'native':
        jid = queue_native(client, factory)
        execute_lane = execute
    else:
        identifier = add_audio(factory) if lane == 'extract-audio' else authorize_image(factory)
        jid = queue_extract(factory, identifier)
        execute_lane = execute_extract
    if failure in {'late-grant-revoke', 'late-membership-revoke'}:
        def revoke():
            with factory() as db:
                if failure == 'late-grant-revoke':
                    db.get(Asset, identifier).metadata_json = {'model_processing_authorized': False}
                else:
                    db.scalar(select(Membership).where(Membership.user_id == 'u')).active = False
                db.commit()
        resident_cloud['during'] = revoke
    else:
        resident_cloud['error'] = provider.ProviderError('模型返回格式无效', usage_receipt={
            'model': 'cloud-vision-fixture' if lane == 'extract-image' else 'cloud-asr-fixture',
            'usage': {'prompt_tokens': 17, 'completion_tokens': 9}, 'output': 'PRIVATE_OUTPUT'})
    completed, job = execute_lane(factory, jid)
    assert not completed and job.status == 'failed' and job.result == {}
    assert len(resident_cloud['calls']) == 1
    assert_one_cloud_receipt(factory, jid, 'cloud-vision-fixture' if lane == 'extract-image' else 'cloud-asr-fixture')
    with factory() as db:
        assert db.get(Asset, identifier).extracted_text == ''
    assert 'PRIVATE_' not in (job.error or '')


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_ordinary_extraction_under_resident_text_uses_cloud_and_does_not_double_bill(skill_client, resident_cloud, kind):
    _, factory, *_ = skill_client
    identifier = authorize_image(factory) if kind == 'image' else add_audio(factory)
    jid = queue_extract(factory, identifier)
    completed, job = execute_extract(factory, jid)
    assert completed and job.status == 'succeeded', job.error
    assert len(resident_cloud['calls']) == 1
    assert_one_cloud_receipt(factory, jid, 'cloud-vision-fixture' if kind == 'image' else 'cloud-asr-fixture')
    with factory() as db:
        asset = db.get(Asset, identifier)
        assert asset.extracted_text == '这是已授权素材的受控转录。'
        assert asset.metadata_json['extraction_verified'] is False


@pytest.mark.parametrize('lane', ['native', 'extract-audio', 'extract-image'])
def test_exhausted_cloud_budget_blocks_before_explicit_provider_call(skill_client, resident_cloud, lane):
    client, factory, *_ = skill_client
    if lane == 'native':
        jid = queue_native(client, factory)
        execute_lane = execute
    else:
        jid = queue_extract(factory, add_audio(factory) if lane == 'extract-audio' else authorize_image(factory))
        execute_lane = execute_extract
    with factory() as db:
        db.add(ModelUsage(tenant_id='t', property_id='p', job_id='earlier-cloud', model='qwen-plus', input_tokens=1000, output_tokens=0))
        db.commit()
    completed, job = execute_lane(factory, jid)
    assert not completed and job.result == {}
    assert resident_cloud['calls'] == []
    assert usage_rows(factory, jid) == []


@pytest.mark.parametrize('lane', ['native', 'extract-audio', 'extract-image'])
def test_missing_supplier_model_uses_explicit_cloud_model_for_accounting(skill_client, resident_cloud, lane):
    client, factory, *_ = skill_client
    resident_cloud['omit_model'] = True
    if lane == 'native':
        jid = queue_native(client, factory)
        execute_lane = execute
    else:
        jid = queue_extract(factory, add_audio(factory) if lane == 'extract-audio' else authorize_image(factory))
        execute_lane = execute_extract
    completed, job = execute_lane(factory, jid)
    assert completed, job.error
    assert_one_cloud_receipt(factory, jid, 'qwen-vl-plus' if lane == 'extract-image' else 'qwen3-asr-flash')


@pytest.mark.parametrize('role', ['editor', 'reviewer'])
def test_extraction_meter_preserves_existing_allowed_roles(skill_client, resident_cloud, role):
    _, factory, *_ = skill_client
    with factory() as db:
        db.scalar(select(Membership).where(Membership.user_id == 'u')).role = role
        db.commit()
    jid = queue_extract(factory, authorize_image(factory))
    completed, job = execute_extract(factory, jid)
    assert completed, job.error
    assert_one_cloud_receipt(factory, jid, 'cloud-vision-fixture')


@pytest.mark.parametrize('role', ['editor', 'reviewer'])
@pytest.mark.parametrize('worker', [False, True])
def test_meter_role_expansion_reads_actual_server_job_kind_not_caller_kind(skill_client, resident_cloud, role, worker):
    from types import SimpleNamespace
    from app import model_usage_ledger as ledger
    from app.worker import claim_job
    client, factory, *_ = skill_client
    queued = run(client, 'social-copywriting', input='整理一段待核验草稿。')
    assert queued.status_code == 200, queued.text
    jid = queued.json()['job_id']
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        assert job.id == jid
        binding = {key: getattr(job, key) for key in ('id', 'tenant_id', 'property_id', 'user_id', 'attempt', 'lease_token')}
    with factory() as db:
        db.scalar(select(Membership).where(Membership.user_id == 'u')).role = role
        db.commit()
    with factory() as db:
        job = db.get(Job, jid)
        if worker:
            db.info['model_usage_recorder'] = ledger.WorkerUsageRecorder(factory, job)
        forged = SimpleNamespace(**binding, kind='extract_asset')
        with pytest.raises(HTTPException) as error:
            ledger.begin_call(db, forged)
        assert error.value.status_code == 403
    assert usage_rows(factory, jid) == []


@pytest.mark.parametrize('revoke', ['grant', 'membership'])
def test_extraction_revocation_before_call_emits_no_request_or_fabricated_receipt(skill_client, resident_cloud, revoke):
    _, factory, *_ = skill_client
    jid = queue_extract(factory, authorize_image(factory))
    with factory() as db:
        if revoke == 'grant':
            db.get(Asset, 'image').metadata_json = {'model_processing_authorized': False}
        else:
            db.scalar(select(Membership).where(Membership.user_id == 'u')).active = False
        db.commit()
    _, job = execute_extract(factory, jid)
    assert resident_cloud['calls'] == []
    assert usage_rows(factory, jid) == []
    assert not job.result or job.result['status'] == 'waiting_authorization'


def test_direct_meter_does_not_autoflush_a_forged_job_kind_before_deciding_roles(skill_client, resident_cloud):
    from app import model_usage_ledger as ledger
    client, factory, *_ = skill_client
    jid = run(client, 'social-copywriting', input='整理待核验草稿。').json()['job_id']
    with factory() as db:
        db.scalar(select(Membership).where(Membership.user_id == 'u')).role = 'editor'
        db.commit()
    with factory() as db:
        job = db.get(Job, jid)
        job.kind = 'extract_asset'
        with pytest.raises(HTTPException) as error:
            ledger.begin_call(db, job)
        assert error.value.status_code == 403
        db.rollback()
    assert usage_rows(factory, jid) == []


@pytest.mark.parametrize('role', ['editor', 'reviewer'])
def test_direct_meter_allows_actual_authorized_extract_role_once(skill_client, resident_cloud, role):
    from app import model_usage_ledger as ledger
    _, factory, *_ = skill_client
    jid = queue_extract(factory, authorize_image(factory))
    with factory() as db:
        db.scalar(select(Membership).where(Membership.user_id == 'u')).role = role
        db.commit()
    with factory() as db:
        job = db.get(Job, jid)
        token = ledger.begin_call(db, job)
        ledger.record_response(db, job, token, response('cloud-vision-fixture'))
        db.commit()
    assert_one_cloud_receipt(factory, jid, 'cloud-vision-fixture')


def test_video_article_cloud_asr_then_native_text_use_distinct_callbacks_and_receipts(skill_client, resident_cloud, monkeypatch):
    client, factory, *_ = skill_client
    native_calls = []
    def native(messages, **kwargs):
        native_calls.append({'messages': messages, **kwargs})
        return response('ppu:text-fixture')
    monkeypatch.setattr(provider, 'chat', native)
    queued = run(client, 'video-to-article', input='', asset_ids=[add_audio(factory)],
                 parameters={'op': 'article', 'engine': 'cloud'})
    assert queued.status_code == 200, queued.text
    jid = queued.json()['job_id']
    completed, job = execute(factory, jid)
    assert completed, job.error
    assert len(resident_cloud['calls']) == len(native_calls) == 1
    assert native_calls[0]['model'] is None
    rows = usage_rows(factory, jid)
    assert sorted(row.model for row in rows) == ['cloud-asr-fixture', 'ppu:text-fixture']
    assert len(job.result['usage_receipts']) == 2


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_ordinary_extraction_missing_explicit_model_cannot_fallback_to_cloud_text(skill_client, resident_cloud, monkeypatch, kind):
    _, factory, *_ = skill_client
    monkeypatch.setattr(settings, 'dashscope_vision_model' if kind == 'image' else 'dashscope_asr_model', '')
    jid = queue_extract(factory, authorize_image(factory) if kind == 'image' else add_audio(factory))
    completed, job = execute_extract(factory, jid)
    assert not completed and job.status == 'failed'
    assert resident_cloud['calls'] == []
    assert usage_rows(factory, jid) == []
