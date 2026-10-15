"""Reject malformed video plans before a native worker job is created."""
import hashlib
import importlib
import json
import wave

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.core_models import Asset
from app.job_models import Job
from test_production_tools import production_env, run


@pytest.fixture
def media_env(production_env):
    db, ctx = production_env
    for index, name in enumerate(['客厅.png', '餐厅.png', '露台.png', '音乐.wav']):
        path = settings.storage_root / name
        if index < 3:
            Image.new('RGB', (16, 16), ('red', 'green', 'blue')[index]).save(path)
        else:
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b'\0\0' * 800)
        db.add(Asset(id=f'media-{index}', tenant_id=ctx.tenant_id, property_id='home', name=name,
            kind='image' if index < 3 else 'audio', storage_key=name,
            mime_type='image/png' if index < 3 else 'audio/wav', byte_size=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), rights='authorized', rights_evidence='拍摄委托授权'))
    db.commit()
    return production_env


def request(parameters, *, skill_id='native:auto-short-video', asset_ids=None):
    return {'skill_id': skill_id, 'asset_ids': asset_ids or [f'media-{index}' for index in range(4)],
            'parameters': parameters}


def test_exact_live_missing_indices_rejects_before_job_with_repairable_asset_map(media_env):
    db, _ = media_env
    bad = request({'storyboard': [{'duration': 10, 'caption': caption} for caption in ['客厅', '餐厅', '露台']],
                   'bgm': 'media-3', 'bgm_index': 0, 'captions': 'true'})
    with pytest.raises(HTTPException) as error:
        run(media_env, 'run_production_skill', bad, raw_text='帮我制作楼盘介绍视频')
    assert error.value.status_code == 422
    assert 'asset_index' in str(error.value.detail)
    assert all(key in str(error.value.detail) for key in ['storyboard[1].asset_index', 'storyboard[2].asset_index',
                                                         'bgm_index', 'bgm', 'captions'])
    assert '0' in str(error.value.detail) and '客厅.png' in str(error.value.detail)
    assert '3' in str(error.value.detail) and '音乐.wav' in str(error.value.detail)
    assert db.scalar(select(Job)) is None


def test_indexed_plan_preserves_full_asset_order_and_enqueues_native_contract(media_env):
    db, _ = media_env
    plan = {'storyboard': [{'asset_index': index, 'duration': 10, 'caption': caption}
                           for index, caption in enumerate(['客厅', '餐厅', '露台'])], 'bgm_index': 3}
    result = run(media_env, 'run_production_skill', request(plan), raw_text='帮我制作楼盘介绍视频')
    job = db.get(Job, result['data']['job_id'])
    assert job.payload['asset_ids'] == ['media-0', 'media-1', 'media-2', 'media-3']
    assert job.payload['parameters']['storyboard'] == plan['storyboard']
    assert job.payload['parameters']['bgm_index'] == 3


@pytest.mark.parametrize(('parameters', 'error_key'), [
    ({'storyboard': [{'duration': 10}]}, 'asset_index'),
    ({'storyboard': [{'asset_index': True}]}, 'asset_index'),
    ({'storyboard': [{'asset_index': 4}]}, 'asset_index'),
    ({'storyboard': [{'asset_index': 3}]}, 'asset_index'),
    ({'storyboard': [{'asset_index': 0, 'duration': 0}]}, 'duration'),
    ({'storyboard': [{'asset_index': 0, 'duration': 601}]}, 'duration'),
    ({'storyboard': [{'asset_index': 0, 'duration': float('nan')}]}, 'duration'),
    ({'storyboard': [{'asset_index': 0, 'start': -1}]}, 'start'),
    ({'storyboard': [{'asset_index': 0, 'start': 21601}]}, 'start'),
    ({'storyboard': {}, 'bgm_index': 3}, 'storyboard'),
    ({'storyboard': [{'asset_index': 0}], 'bgm_index': 0}, 'bgm_index'),
    ({'storyboard': [{'asset_index': 0}], 'bgm_index': True}, 'bgm_index'),
    ({'storyboard': [{'asset_index': 0}], 'bgm': 'media-3'}, 'bgm'),
    ({'storyboard': [{'asset_index': 0}], 'bgm': '/private/music.wav'}, 'bgm'),
    ({'storyboard': [{'asset_index': 0}], 'captions': 'true'}, 'captions'),
])
def test_invalid_nested_plan_is_rejected_without_queued_job(media_env, parameters, error_key):
    db, _ = media_env
    with pytest.raises(HTTPException) as error:
        run(media_env, 'run_production_skill', request(parameters), raw_text='帮我制作视频')
    assert error.value.status_code == 422
    assert error_key in str(error.value.detail)
    assert '音乐.wav' in str(error.value.detail)
    assert '/private/music.wav' not in str(error.value.detail)
    assert db.scalar(select(Job)) is None


def test_indices_refer_to_original_order_without_visual_only_reindexing(media_env):
    db, _ = media_env
    with pytest.raises(HTTPException) as error:
        run(media_env, 'run_production_skill', request({'storyboard': [{'asset_index': 0}], 'bgm_index': 3},
            asset_ids=['media-3', 'media-0', 'media-1', 'media-2']), raw_text='制作视频')
    assert error.value.status_code == 422
    assert db.scalar(select(Job)) is None
    result = run(media_env, 'run_production_skill', request({'storyboard': [{'asset_index': 1}], 'bgm_index': 0},
        asset_ids=['media-3', 'media-0', 'media-1', 'media-2']), raw_text='制作视频')
    assert db.get(Job, result['data']['job_id']).payload['parameters']['storyboard'][0]['asset_index'] == 1


@pytest.mark.parametrize('skill_id', ['native:auto-short-video', 'slideshow-video'])
def test_existing_per_and_captions_forms_work_without_storyboard(media_env, skill_id):
    db, _ = media_env
    result = run(media_env, 'run_production_skill', request({'per': 3, 'captions': ['客厅', '餐厅', '露台'],
                                                         'bgm_index': 3}, skill_id=skill_id), raw_text='制作相册视频')
    assert db.get(Job, result['data']['job_id']).payload['parameters']['captions'] == ['客厅', '餐厅', '露台']


def test_auto_video_empty_storyboard_form_default_keeps_native_per_mode(media_env):
    db, _ = media_env
    result = run(media_env, 'run_production_skill', request({'storyboard': [], 'per': 3, 'bgm_index': 3}),
                 raw_text='制作视频')
    assert db.get(Job, result['data']['job_id']).payload['parameters']['storyboard'] == []


def test_direct_native_route_rejects_bad_plan_before_commit_and_queues_correct_plan(media_env):
    from app.native_skills import SkillRun, run_skill
    db, ctx = media_env
    bad = SkillRun(asset_ids=['media-0', 'media-3'], parameters={'storyboard': [{'caption': '缺索引'}]})
    with pytest.raises(HTTPException) as error:
        run_skill('home', 'native:auto-short-video', bad, ctx=ctx, db=db)
    assert error.value.status_code == 422 and db.scalar(select(Job)) is None
    good = SkillRun(asset_ids=['media-0', 'media-3'], parameters={
        'storyboard': json.dumps([{'asset_index': 0, 'duration': 1}]), 'bgm_index': 1})
    result = run_skill('home', 'native:auto-short-video', good, ctx=ctx, db=db)
    assert db.get(Job, result['job_id']).payload['parameters']['storyboard'] == [{'asset_index': 0, 'duration': 1}]


def test_capability_nested_contract_examples_are_accepted_by_shared_validator(media_env):
    module = importlib.import_module('app.media_plan_contract')
    assets = [{'id': f'media-{index}', 'kind': 'image' if index < 3 else 'audio', 'name': str(index)} for index in range(4)]
    for identifier in ['native:auto-short-video', 'slideshow-video']:
        row = run(media_env, 'get_production_capabilities', {'skill_id': identifier})['data']['skills'][0]
        contract = row['agent_contract']
        accepted = module.validate_media_plan(row['skill_name'], contract['example']['parameters'], assets)
        assert accepted['bgm_index'] == 3
        assert contract['asset_order'] == 'asset_ids_exact_order_zero_based'
        if row['skill_name'] == 'auto-short-video':
            assert contract['parameters']['properties']['storyboard']['items']['required'] == ['asset_index']
    search = run(media_env, 'get_production_capabilities', {'query': '相册视频'})['data']['skills']
    assert any(row.get('agent_contract') for row in search)
