"""Authorized source media yields traceable sampled evidence, never target facts."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import subprocess
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import provider
from app.config import settings
from app.core_models import Asset, Membership, Property, Tenant, User
from app.db import Base, get_db
from app.job_models import Job
from app.operations_models import ContentOpportunity
from app.security import Context, get_context


BASE = '/api/projects/p/operations/research'
SOURCE = 'https://www.douyin.com/video/7390000000000000001'


def test_analysis_notes_preserve_actual_timing_and_never_expand_frame_coverage():
    from app.reference_media import analysis_notes
    notes = analysis_notes({'schema_version': 2,
        'transcript_segments': [{'id': 't001', 'start_seconds': 0.25, 'end_seconds': 2.75,
                                 'text': '为什么先看动线？'}],
        'frames': [{'id': 'f001', 'time_seconds': 1.125, 'observation': '客厅，右侧可见门洞'}]})
    assert notes == {'transcript': '[00:00.250-00:02.750] 为什么先看动线？',
                     'observations': '[00:01.125] 客厅，右侧可见门洞'}


def test_analysis_notes_reject_overflow_instead_of_dropping_source_evidence():
    from app.reference_media import analysis_notes
    with pytest.raises(ValueError, match='10000'):
        analysis_notes({'schema_version': 2, 'transcript_segments': [
            {'id': 't001', 'start_seconds': 0, 'end_seconds': 30, 'text': '字' * 10000}], 'frames': []})


def test_local_asr_explicit_empty_support_preserves_strict_subtitle_default(tmp_path, monkeypatch):
    from app.content_skills.tools import speech
    import faster_whisper
    import wave
    audio = tmp_path / 'silence.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\0' * 32000)
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'model.bin').write_bytes(b'test-local-model')
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    monkeypatch.setattr(settings, 'native_whisper_model_path', model)
    class EmptyASR:
        def __init__(self, *args, **kwargs): pass
        def transcribe(self, *args, **kwargs): return iter([]), None
    monkeypatch.setattr(faster_whisper, 'WhisperModel', EmptyASR)
    with pytest.raises(ValueError, match='字幕segments'):
        speech.timed_transcript({'path': str(audio)}, {}, {})
    assert speech.timed_transcript({'path': str(audio)}, {}, {}, allow_empty=True) == []


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    from app.reference_media import router
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='其他团队'),
                User(id='u', name='编辑', email='media@example.org', password_hash='x')])
    db.flush()
    db.add_all([Membership(id='m', user_id='u', tenant_id='t', role='content', active=True),
                Property(id='p', tenant_id='t', name='住宅', city='苏州'),
                Property(id='p2', tenant_id='t', name='其他住宅', city='杭州')])
    db.flush()
    video = tmp_path / 'source.mp4'
    subprocess.run([settings.ffmpeg_binary, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=blue:s=160x90:r=10', '-t', '2', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(video)], check=True)
    asset = Asset(id='a', tenant_id='t', property_id='p', name='参考视频.mp4', kind='video',
                  mime_type='video/mp4', storage_key='source.mp4', sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
                  byte_size=video.stat().st_size, rights='authorized', rights_evidence='作者授权研究此原片',
                  rights_revision=1, origin_type='reference_only', metadata_json={'model_processing_authorized': True})
    reference = ContentOpportunity(id='r', tenant_id='t', property_id='p', title='空间导览',
        kind='competitor', platform='抖音', source_url=SOURCE, summary='', status='saved',
        media_asset_id='a', revision=1, analysis_snapshot={}, research_notes={}, source_snapshot={})
    db.add_all([asset, reference])
    db.commit()
    ctx = Context('u', 't', 'content', '编辑', 'media@example.org')
    current = {'ctx': ctx}
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_context] = lambda: current['ctx']
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-reference-service')
    with TestClient(app) as client:
        yield client, db, current, ctx, asset, reference
    db.close()
    engine.dispose()


def start(env, **changes):
    client, *_ = env
    return client.post(BASE + '/references/r/analyze', json={
        'expected_revision': 1, 'idempotency_key': str(uuid4()), **changes})


def external_response(messages, **kwargs):
    evidence = json.loads(messages[1]['content'][0]['text'])
    frames = evidence['frames']
    return {'model': 'qwen3-vl-plus', 'choices': [{'finish_reason': 'stop', 'message': {
        'role': 'assistant', 'content': json.dumps({
            'frames': [{'id': frame['id'], 'observation': '蓝色测试画面，无可识别文字'} for frame in frames],
            'techniques': [{'id': 'evidence_sequence', 'reason': '采样画面保持同一测试场景',
                            'evidence_refs': [frames[0]['id']]}],
            'summary': '仅可观察到蓝色测试画面，无法判断真实住宅信息。',
            'cta': {'text': '', 'evidence_refs': []},
            'pacing': {'hypothesis': '采样画面一致，不能据此判断剪辑速度。',
                       'evidence_refs': [frame['id'] for frame in frames]}}, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 51, 'completion_tokens': 33}}


def test_analysis_start_is_idempotent_and_binds_server_source_and_asset(media_env):
    client, db, *_ = media_env
    key = str(uuid4())
    first = start(media_env, idempotency_key=key)
    assert first.status_code == 200, first.text
    second = start(media_env, idempotency_key=key)
    assert second.json()['job_id'] == first.json()['job_id']
    jobs = db.scalars(select(Job)).all()
    assert len(jobs) == 1
    assert jobs[0].kind == 'reference_analyze'
    assert jobs[0].payload['source_url'] == SOURCE
    assert jobs[0].payload['asset_sha256'] == media_env[4].sha256
    assert jobs[0].payload['rights_revision'] == 1


@pytest.mark.parametrize('mutation,status', [('revoke', 409), ('ai', 409), ('wrong_project', 404),
    ('revision', 409), ('viewer', 403), ('other_tenant', 404), ('oversize', 422)])
def test_analysis_start_rejects_unavailable_or_unauthorized_evidence(media_env, mutation, status):
    _, db, current, ctx, asset, reference = media_env
    if mutation == 'revoke': asset.rights = 'revoked'
    elif mutation == 'ai': asset.metadata_json = {'model_processing_authorized': False}
    elif mutation == 'wrong_project': asset.property_id = 'p2'
    elif mutation == 'revision': reference.revision = 2
    elif mutation == 'viewer': current['ctx'] = replace(ctx, role='viewer')
    elif mutation == 'other_tenant': current['ctx'] = replace(ctx, tenant_id='other')
    elif mutation == 'oversize': asset.byte_size = 104857601
    db.commit()
    response = start(media_env)
    assert response.status_code == status, response.text
    assert db.scalar(select(Job)) is None


def test_silent_video_analysis_has_actual_frames_usage_and_no_fabricated_transcript(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    from app.agent_models import ModelUsage
    client, db, _, _, _, reference = media_env
    monkeypatch.setattr(provider, 'cloud_chat', external_response)
    response = start(media_env)
    assert response.status_code == 200, response.text
    job = db.get(Job, response.json()['job_id'])
    result = handle_reference_analyze(db, job)
    job.status, job.result = 'succeeded', result
    db.commit()
    analysis = result['reference_analysis']
    assert analysis['schema_version'] == 2
    assert analysis['source']['watched'] is False
    assert analysis['source']['evidence_basis'] == 'asset_asr_and_sampled_frames'
    assert analysis['transcript_segments'] == []
    assert 1 <= len(analysis['frames']) <= 8
    assert all(0 <= frame['time_seconds'] < 2 for frame in analysis['frames'])
    assert all(frame['observation'] == '蓝色测试画面，无可识别文字' for frame in analysis['frames'])
    assert analysis['duration_seconds'] == 2
    assert reference.analysis_snapshot == analysis
    assert reference.research_notes == {}
    assert db.scalar(select(ModelUsage)).input_tokens == 51
    frame = client.get(analysis['frames'][0]['frame_url'])
    assert frame.status_code == 200
    assert frame.headers['content-type'] == 'image/jpeg'
    assert 'base64' not in json.dumps(analysis)
    assert str(settings.storage_root) not in json.dumps(analysis)


def test_model_cannot_invent_frame_ids_or_timestamps(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    _, db, *_ = media_env
    def fabricated(messages, **kwargs):
        response = external_response(messages, **kwargs)
        response['choices'][0]['message']['content'] = json.dumps({
            'frames': [{'id': 'f999', 'time_seconds': 999, 'observation': '虚构画面'}],
            'techniques': [], 'summary': '', 'cta': {'text': '', 'evidence_refs': []},
            'pacing': {'hypothesis': '', 'evidence_refs': []}})
        return response
    monkeypatch.setattr(provider, 'cloud_chat', fabricated)
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])
    with pytest.raises(ValueError, match='视觉'):
        handle_reference_analyze(db, job)
    assert media_env[5].analysis_snapshot == {}


def test_edit_during_model_call_prevents_stale_analysis_write_and_retains_usage(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    from app.agent_models import ModelUsage
    _, db, _, _, _, reference = media_env
    def concurrent_edit(messages, **kwargs):
        reference.revision = 2
        reference.research_notes = {'transcript': '用户刚修改的笔记'}
        db.flush()
        return external_response(messages, **kwargs)
    monkeypatch.setattr(provider, 'cloud_chat', concurrent_edit)
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        handle_reference_analyze(db, job)
    assert error.value.status_code == 409
    assert reference.analysis_snapshot == {}
    assert reference.research_notes == {'transcript': '用户刚修改的笔记'}
    assert db.scalar(select(ModelUsage)).input_tokens == 51


@pytest.mark.parametrize('change', ['revoked', 'cancelled', 'source_changed'])
def test_model_completion_cannot_publish_revoked_cancelled_or_rebound_evidence(media_env, monkeypatch, change):
    from app.reference_media import handle_reference_analyze
    from app.agent_models import ModelUsage
    from fastapi import HTTPException
    from app.worker import LostLease
    _, db, _, _, asset, reference = media_env
    def external(messages, **kwargs):
        if change == 'revoked':
            asset.rights = 'revoked'
            asset.rights_revision += 1
            db.flush()
        elif change == 'source_changed':
            reference.source_url = 'https://www.douyin.com/video/7390000000000000002'
            db.flush()
        else:
            db.info['job_cancelled'] = lambda: True
        return external_response(messages, **kwargs)
    monkeypatch.setattr(provider, 'cloud_chat', external)
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])
    with pytest.raises((HTTPException, LostLease)):
        handle_reference_analyze(db, job)
    assert reference.analysis_snapshot == {}
    assert db.scalar(select(ModelUsage)).input_tokens == 51


def test_frame_times_are_decoded_pts_instead_of_requested_seek_times(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    client, db, _, _, asset, _ = media_env
    path = settings.storage_root / asset.storage_key
    subprocess.run([settings.ffmpeg_binary, '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=green:s=160x90:r=3', '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True)
    asset.sha256, asset.byte_size = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size
    db.commit()
    monkeypatch.setattr(provider, 'cloud_chat', external_response)
    response = start(media_env)
    analysis = handle_reference_analyze(db, db.get(Job, response.json()['job_id']))['reference_analysis']
    measured = [frame['time_seconds'] for frame in analysis['frames']]
    assert any(abs(value - 2/3) < .0001 for value in measured)
    assert .5 not in measured
    assert len(measured) == len(set(measured))


def test_cache_reuse_revalidates_original_file_integrity(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    client, db, _, _, asset, _ = media_env
    monkeypatch.setattr(provider, 'cloud_chat', external_response)
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])
    job.result, job.status = handle_reference_analyze(db, job), 'succeeded'
    db.commit()
    (settings.storage_root / asset.storage_key).write_bytes(b'changed-video')
    reused = start(media_env)
    assert reused.status_code == 409, reused.text


def test_editorial_notes_keep_source_frames_and_cache_without_overwriting_notes(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    client, db, _, _, _, reference = media_env
    monkeypatch.setattr(provider, 'cloud_chat', external_response)
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])
    job.result, job.status = handle_reference_analyze(db, job), 'succeeded'
    db.commit()
    reference.revision = 2
    reference.research_notes = {'transcript': '编辑校订口播'}
    db.commit()
    assert client.get(job.result['reference_analysis']['frames'][0]['frame_url']).status_code == 200
    reused = start(media_env, expected_revision=2)
    assert reused.status_code == 200, reused.text
    assert reused.json() == {'job_id': job.id, 'cached': True}
    assert reference.research_notes == {'transcript': '编辑校订口播'}
    forced = start(media_env, expected_revision=2, force=True)
    assert forced.status_code == 200, forced.text
    assert forced.json()['job_id'] != job.id


def test_explicit_force_is_still_deduplicated_by_idempotency_key(media_env):
    _, db, *_ = media_env
    key = str(uuid4())
    first = start(media_env, idempotency_key=key, force=True)
    assert first.status_code == 200, first.text
    second = start(media_env, idempotency_key=key, force=True)
    assert second.json()['job_id'] == first.json()['job_id']
    assert len(db.scalars(select(Job)).all()) == 1


def _historical_analysis(env, identity, key, *, status='failed', offset=0, **changes):
    _, db, _, _, asset, reference = env
    payload = {'reference_id': 'r', 'reference_revision': 1, 'source_url': SOURCE,
        'source_title': '空间导览', 'asset_id': 'a', 'asset_sha256': asset.sha256,
        'asset_byte_size': asset.byte_size, 'rights_revision': 1, 'idempotency_key': key}
    job = Job(id=identity, tenant_id='t', property_id='p', user_id='u', kind='reference_analyze',
        status=status, payload=payload | changes.pop('payload', {}), result={},
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=offset), **changes)
    db.add(job)
    return job


def _analysis_rows(db):
    return {row.id: (row.status, json.dumps(row.payload, sort_keys=True))
            for row in db.scalars(select(Job)).all()}


@pytest.mark.parametrize('original_status', ['failed', 'succeeded'])
def test_analysis_retry_keeps_original_key_beyond_recent_cache(media_env, original_status):
    from app.agent_models import ModelUsage
    _, db, *_ = media_env
    key = str(uuid4())
    _historical_analysis(media_env, 'original', key, status=original_status)
    for index in range(200):
        _historical_analysis(media_env, f'newer-{index:03d}', str(uuid4()), offset=index + 1)
    db.commit()
    before = _analysis_rows(db)

    response = start(media_env, idempotency_key=key, force=True)

    assert response.status_code == 200, response.text
    assert response.json() == {'job_id': 'original', 'cached': original_status == 'succeeded'}
    assert _analysis_rows(db) == before
    assert db.scalar(select(ModelUsage)) is None


@pytest.mark.parametrize('changed_binding', [
    {'reference_revision': 2}, {'source_url': 'https://www.douyin.com/video/7390000000000000002'},
    {'source_title': '另一个原片标题'}, {'asset_id': 'previous-asset'},
    {'asset_sha256': 'b' * 64}, {'asset_byte_size': 1}, {'rights_revision': 2},
])
def test_newer_cache_cannot_hide_existing_key_binding_conflict(media_env, changed_binding):
    _, db, *_ = media_env
    key = str(uuid4())
    _historical_analysis(media_env, 'original', key, payload=changed_binding)
    _historical_analysis(media_env, 'newer-cache', str(uuid4()), status='succeeded', offset=1)
    db.commit()
    before = _analysis_rows(db)

    response = start(media_env, idempotency_key=key)

    assert response.status_code == 409, response.text
    assert _analysis_rows(db) == before


@pytest.mark.parametrize('foreign_scope', ['tenant', 'project', 'reference', 'kind'])
def test_analysis_key_lookup_cannot_reuse_another_scope(media_env, foreign_scope):
    _, db, *_ = media_env
    key = str(uuid4())
    foreign = _historical_analysis(media_env, 'foreign', key)
    if foreign_scope == 'tenant':
        foreign.tenant_id = 'other'
    elif foreign_scope == 'project':
        foreign.property_id = 'p2'
    elif foreign_scope == 'reference':
        foreign.payload = foreign.payload | {'reference_id': 'other-reference'}
    else:
        foreign.kind = 'generate_content'
    db.commit()
    before = _analysis_rows(db)

    response = start(media_env, idempotency_key=key, force=True)

    assert response.status_code == 200, response.text
    assert response.json()['job_id'] != 'foreign'
    queued = db.get(Job, response.json()['job_id'])
    assert (queued.tenant_id, queued.property_id, queued.kind, queued.status) == (
        't', 'p', 'reference_analyze', 'queued')
    assert queued.payload['reference_id'] == 'r'
    assert queued.payload['idempotency_key'] == key
    assert len(_analysis_rows(db)) == 2
    assert _analysis_rows(db)['foreign'] == before['foreign']


@pytest.mark.parametrize('same_created_at', [False, True])
@pytest.mark.parametrize('oldest_binding_matches', [False, True])
def test_duplicate_legacy_keys_keep_the_earliest_binding(media_env, same_created_at, oldest_binding_matches):
    _, db, *_ = media_env
    key = str(uuid4())
    # Insert the newer duplicate first so an unordered query cannot pass by luck.
    _historical_analysis(media_env, 'duplicate-b', key, status='succeeded',
        offset=0 if same_created_at else 1,
        payload={'source_title': '旧的不同标题'} if oldest_binding_matches else {})
    _historical_analysis(media_env, 'duplicate-a', key,
        payload={} if oldest_binding_matches else {'source_title': '旧的不同标题'})
    db.commit()
    before = _analysis_rows(db)

    response = start(media_env, idempotency_key=key, force=True)

    if oldest_binding_matches:
        assert response.status_code == 200, response.text
        assert response.json() == {'job_id': 'duplicate-a', 'cached': False}
    else:
        assert response.status_code == 409, response.text
    assert _analysis_rows(db) == before


def test_unrelated_key_does_not_expand_the_success_cache_window(media_env):
    _, db, *_ = media_env
    _historical_analysis(media_env, 'old-success', str(uuid4()), status='succeeded')
    for index in range(200):
        _historical_analysis(media_env, f'newer-{index:03d}', str(uuid4()), offset=index + 1)
    db.commit()
    before = _analysis_rows(db)

    response = start(media_env)

    assert response.status_code == 200, response.text
    assert response.json()['job_id'] not in before
    assert response.json()['cached'] is False
    assert len(_analysis_rows(db)) == 202


def test_budget_gate_prevents_cloud_call_after_daily_limit(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    from app.agent_models import ModelUsage
    from fastapi import HTTPException
    _, db, *_ = media_env
    monkeypatch.setattr(settings, 'development_token_budget', 1000)
    monkeypatch.setattr(settings, 'app_env', 'development')
    db.add(ModelUsage(id='budget', tenant_id='t', property_id='p', job_id='old-job',
                      model='qwen3-vl-plus', input_tokens=1000, output_tokens=0))
    db.commit()
    def forbidden(*args, **kwargs):
        pytest.fail('Daily budget must block the external inference request')
    monkeypatch.setattr(provider, 'cloud_chat', forbidden)
    response = start(media_env)
    with pytest.raises(HTTPException) as error:
        handle_reference_analyze(db, db.get(Job, response.json()['job_id']))
    assert error.value.status_code == 429
    assert media_env[5].analysis_snapshot == {}


def test_old_attempt_cleanup_cannot_delete_reclaimed_attempt_frame_bytes(media_env):
    from app.reference_media import _sample_frames
    from app.worker import _cleanup_outputs
    from pathlib import Path
    client, db, _, _, asset, _ = media_env
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])
    job.attempt = 1
    db.commit()
    _sample_frames(db, job, settings.storage_root / asset.storage_key, 2)
    old_outputs = db.info.pop('created_private_files')
    job.attempt = 2
    db.commit()
    new_frames = _sample_frames(db, job, settings.storage_root / asset.storage_key, 2)
    new_outputs = db.info.pop('created_private_files')
    expected_bytes = Path(new_outputs[0]).read_bytes()
    db.info['created_private_files'] = old_outputs
    _cleanup_outputs(db)
    assert Path(new_outputs[0]).is_file(), 'Expired attempt cleanup removed the reclaimed attempt frame'
    assert Path(new_outputs[0]).read_bytes() == expected_bytes
    assert set(old_outputs).isdisjoint(new_outputs)
    job.status, job.lease_token = 'succeeded', None
    job.result = {'reference_analysis': {'artifact_attempt': 2, 'frames': new_frames}}
    db.commit()
    preview = client.get(BASE + f'/references/r/frames/{job.id}/f001')
    assert preview.status_code == 200
    assert preview.content == expected_bytes
