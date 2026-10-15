"""Long reference analysis preserves timed evidence in bounded model batches."""
import hashlib
import json
import subprocess

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import provider
from app.agent_models import ModelUsage
from app.config import settings
from app.core_models import Membership
from app.job_models import Job
from test_reference_media import external_response, media_env, start


def long_source(env, *, audio=False, duration=120):
    _, db, _, _, asset, _ = env
    path = settings.storage_root / asset.storage_key
    command = [settings.ffmpeg_binary, '-v', 'error', '-y', '-f', 'lavfi', '-i',
               'color=c=blue:s=160x90:r=2']
    if audio:
        command += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000']
    command += ['-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
    if audio:
        command += ['-c:a', 'aac']
    subprocess.run([*command, str(path)], check=True)
    asset.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    asset.byte_size = path.stat().st_size
    db.commit()
    return path


def test_long_reference_samples_middle_and_tail_with_decoded_time_coverage(media_env):
    from app.reference_media import _sample_frames
    path = long_source(media_env)
    _, db, *_ = media_env
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])

    frames = _sample_frames(db, job, path, 120)
    times = [frame['time_seconds'] for frame in frames]

    assert 14 <= len(frames) <= 64
    assert times[0] == 0
    assert any(0 < value <= .5 for value in times)
    assert times[-1] >= 119
    assert max(right-left for left, right in zip(times, times[1:])) <= 13
    assert len(times) == len(set(times))


def test_ten_minute_reference_stays_bounded_without_dropping_middle_or_tail(media_env):
    from app.reference_media import _sample_frames, capabilities
    path = long_source(media_env, duration=600)
    _, db, *_ = media_env
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])

    frames = _sample_frames(db, job, path, 600)
    times = [frame['time_seconds'] for frame in frames]

    assert 48 <= len(frames) <= 64
    assert times[-1] >= 599
    assert max(right-left for left, right in zip(times, times[1:])) <= 13
    limits = capabilities()['video_analysis']
    assert limits['max_vision_calls'] <= 8
    assert limits['max_vision_frames_per_call'] <= 8


def test_beats_use_instant_and_asr_bounds_and_do_not_duplicate_the_same_evidence():
    from app.reference_media import _timed_beats
    frames = [{'id': 'f001', 'time_seconds': 1.25}, {'id': 'f002', 'time_seconds': 19.5}]
    transcript = [{'id': 't001', 'start_seconds': 10, 'end_seconds': 14, 'text': '真实转写段落'}]
    rows = [{'label': label, 'purpose': '待核对叙事作用', 'summary': '只依据引用证据',
             'evidence_refs': refs, 'status': 'hypothesis'} for label, refs in [
        ('混合证据', ['f002', 't001']), ('采样瞬间', ['f001']), ('重复表述', ['f001'])]]

    result = _timed_beats(rows, frames, transcript)

    assert len(result) == 2
    assert [(row['id'], row['start_seconds'], row['end_seconds']) for row in result] == [
        ('b001', 1.25, 1.25), ('b002', 10, 19.5)]


def test_legacy_visual_reply_groups_evidence_as_hypotheses_even_without_description(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    _, db, *_ = media_env

    def model(messages, **kwargs):
        response = external_response(messages, **kwargs)
        value = json.loads(response['choices'][0]['message']['content'])
        for frame in value['frames']:
            frame['observation'] = ''
        response['choices'][0]['message']['content'] = json.dumps(value)
        return response

    monkeypatch.setattr(provider, 'cloud_chat', model)
    response = start(media_env)
    analysis = handle_reference_analyze(db, db.get(Job, response.json()['job_id']))['reference_analysis']

    assert analysis['narrative_beats']
    assert all(row['status'] == 'hypothesis' and row['summary'].replace('/', '').strip()
               for row in analysis['narrative_beats'])
    assert {ref for row in analysis['narrative_beats'] for ref in row['evidence_refs']} == {
        row['id'] for row in analysis['frames']}


def test_long_analysis_keeps_later_batch_evidence_for_merged_pacing(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    long_source(media_env, duration=600)
    _, db, *_ = media_env
    monkeypatch.setattr(provider, 'cloud_chat', external_response)
    response = start(media_env)

    analysis = handle_reference_analyze(db, db.get(Job, response.json()['job_id']))['reference_analysis']

    assert len(analysis['frames']) >= 48
    assert set(analysis['pacing']['evidence_refs']) == {frame['id'] for frame in analysis['frames']}


def test_asr_segment_entirely_beyond_video_tail_cannot_become_inverted_evidence(media_env, monkeypatch):
    from app.reference_media import _transcript, speech
    _, db, _, _, asset, _ = media_env
    monkeypatch.setattr(speech, 'local_asr_ready', lambda: True)
    monkeypatch.setattr(speech, 'timed_transcript', lambda *args, **kwargs: [
        {'start': 2.05, 'end': 2.10, 'text': '不属于原片时长的段落'}])
    response = start(media_env)
    job = db.get(Job, response.json()['job_id'])

    with pytest.raises(ValueError, match='转写时间'):
        _transcript(db, job, settings.storage_root / asset.storage_key, 2, True)


def test_long_analysis_batches_frames_and_builds_beats_from_actual_evidence_bounds(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze, speech
    long_source(media_env, audio=True)
    _, db, _, _, _, reference = media_env
    monkeypatch.setattr(speech, 'local_asr_ready', lambda: True)
    monkeypatch.setattr(speech, 'timed_transcript', lambda *args, **kwargs: [
        {'start': index * 12, 'end': index * 12 + 8, 'text': f'第{index+1}段先提问，再核对条件。'}
        for index in range(10)])
    calls = []

    def model(messages, **kwargs):
        evidence = json.loads(messages[1]['content'][0]['text'])
        calls.append(evidence)
        assert len(evidence['frames']) <= 8
        assert len([part for part in messages[1]['content'] if part['type'] == 'image_url']) <= 8
        response = external_response(messages, **kwargs)
        value = json.loads(response['choices'][0]['message']['content'])
        value['narrative_beats'] = [{'label': '问题与核对', 'purpose': '用问题引出核对条件，仍需人工确认',
            'summary': '本段有真实转写与蓝色采样画面。',
            'evidence_refs': [evidence['frames'][0]['id'], evidence['transcript_segments'][0]['id']],
            'status': 'hypothesis'}]
        response['choices'][0]['message']['content'] = json.dumps(value, ensure_ascii=False)
        return response

    monkeypatch.setattr(provider, 'cloud_chat', model)
    response = start(media_env)
    analysis = handle_reference_analyze(db, db.get(Job, response.json()['job_id']))['reference_analysis']

    assert len(calls) == 2
    assert len(analysis['frames']) >= 14
    assert len(analysis['transcript_segments']) == 10
    assert analysis['coverage']['vision_batch_count'] == 2
    assert analysis['coverage']['transcript_segments_used'] == 10
    assert {row['id'] for call in calls for row in call['transcript_segments']} == {f't{i:03d}' for i in range(1, 11)}
    assert {row['id'] for row in calls[0]['transcript_segments']} != {row['id'] for row in calls[1]['transcript_segments']}
    evidence_bounds = {frame['id']: (frame['time_seconds'], frame['time_seconds']) for frame in analysis['frames']}
    evidence_bounds.update({row['id']: (row['start_seconds'], row['end_seconds']) for row in analysis['transcript_segments']})
    assert [beat['id'] for beat in analysis['narrative_beats']] == ['b001', 'b002']
    for beat in analysis['narrative_beats']:
        assert beat['start_seconds'] == min(evidence_bounds[ref][0] for ref in beat['evidence_refs'])
        assert beat['end_seconds'] == max(evidence_bounds[ref][1] for ref in beat['evidence_refs'])
        assert beat['status'] == 'hypothesis'
    assert reference.analysis_snapshot == analysis
    assert analysis['source']['watched'] is False
    assert len(db.scalars(select(ModelUsage)).all()) == 2


def test_each_vision_batch_checks_remaining_budget_and_keeps_consumed_usage(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    long_source(media_env)
    _, db, *_ = media_env
    monkeypatch.setattr(settings, 'app_env', 'development')
    monkeypatch.setattr(settings, 'development_token_budget', 1000)
    calls = []

    def model(messages, **kwargs):
        calls.append(messages)
        response = external_response(messages, **kwargs)
        response['usage'] = {'prompt_tokens': 1000, 'completion_tokens': 0}
        return response

    monkeypatch.setattr(provider, 'cloud_chat', model)
    response = start(media_env)
    with pytest.raises(HTTPException) as error:
        handle_reference_analyze(db, db.get(Job, response.json()['job_id']))
    assert error.value.status_code == 429
    assert len(calls) == 1
    assert db.scalar(select(ModelUsage)).input_tokens == 1000
    assert media_env[5].analysis_snapshot == {}


@pytest.mark.parametrize('change', ['cancelled', 'rights', 'source_sha', 'membership'])
def test_long_analysis_stops_after_batch_when_evidence_or_permission_changes(media_env, monkeypatch, change):
    from app.reference_media import handle_reference_analyze
    from app.worker import LostLease
    long_source(media_env)
    _, db, _, _, asset, reference = media_env
    calls = []

    def model(messages, **kwargs):
        calls.append(messages)
        response = external_response(messages, **kwargs)
        if change == 'cancelled':
            db.info['job_cancelled'] = lambda: True
        elif change == 'rights':
            asset.rights = 'revoked'
            asset.rights_revision += 1
        elif change == 'source_sha':
            asset.sha256 = 'changed-source-digest'
        else:
            db.get(Membership, 'm').role = 'viewer'
        db.flush()
        return response

    monkeypatch.setattr(provider, 'cloud_chat', model)
    response = start(media_env)
    with pytest.raises((HTTPException, LostLease)):
        handle_reference_analyze(db, db.get(Job, response.json()['job_id']))

    assert len(calls) == 1
    assert len(db.scalars(select(ModelUsage)).all()) == 1
    assert reference.analysis_snapshot == {}


def test_invalid_later_batch_keeps_all_usage_without_saving_partial_analysis(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    long_source(media_env)
    _, db, *_ = media_env
    calls = []

    def model(messages, **kwargs):
        calls.append(messages)
        response = external_response(messages, **kwargs)
        if len(calls) == 2:
            value = json.loads(response['choices'][0]['message']['content'])
            value['narrative_beats'] = [{'label': '无依据段落', 'purpose': '待核对', 'summary': '未知',
                                        'evidence_refs': ['f999'], 'status': 'hypothesis'}]
            response['choices'][0]['message']['content'] = json.dumps(value)
        return response

    monkeypatch.setattr(provider, 'cloud_chat', model)
    response = start(media_env)
    with pytest.raises(ValueError, match='视觉'):
        handle_reference_analyze(db, db.get(Job, response.json()['job_id']))

    assert len(calls) == 2
    assert len(db.scalars(select(ModelUsage)).all()) == 2
    assert media_env[5].analysis_snapshot == {}


def test_narrative_beat_cannot_reference_frames_from_another_batch(media_env, monkeypatch):
    from app.reference_media import handle_reference_analyze
    long_source(media_env)
    _, db, *_ = media_env

    def model(messages, **kwargs):
        response = external_response(messages, **kwargs)
        value = json.loads(response['choices'][0]['message']['content'])
        value['narrative_beats'] = [{'label': '未提供的下一段', 'purpose': '待核对', 'summary': '未知',
                                    'evidence_refs': ['f009'], 'status': 'hypothesis'}]
        response['choices'][0]['message']['content'] = json.dumps(value)
        return response

    monkeypatch.setattr(provider, 'cloud_chat', model)
    response = start(media_env)
    with pytest.raises(ValueError, match='视觉'):
        handle_reference_analyze(db, db.get(Job, response.json()['job_id']))
    assert media_env[5].analysis_snapshot == {}


@pytest.mark.parametrize('invented', [{'id': 'model-beat'}, {'start_seconds': 99}, {'status': 'proven'}])
def test_model_cannot_own_beat_ids_times_or_invent_evidence_status(media_env, monkeypatch, invented):
    from app.reference_media import handle_reference_analyze
    _, db, *_ = media_env

    def model(messages, **kwargs):
        response = external_response(messages, **kwargs)
        value = json.loads(response['choices'][0]['message']['content'])
        value['narrative_beats'] = [{'label': '采样瞬间', 'purpose': '待核对', 'summary': '蓝色画面',
                                    'evidence_refs': ['f001'], 'status': 'hypothesis', **invented}]
        response['choices'][0]['message']['content'] = json.dumps(value)
        return response

    monkeypatch.setattr(provider, 'cloud_chat', model)
    response = start(media_env)
    with pytest.raises(ValueError, match='视觉'):
        handle_reference_analyze(db, db.get(Job, response.json()['job_id']))
    assert media_env[5].analysis_snapshot == {}
