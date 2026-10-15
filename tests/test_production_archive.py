"""Real artifact-to-library archival keeps provenance and remains transactional."""
import hashlib
import importlib
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core_models import Asset, Membership, Property, Tenant, User
from app.db import Base
from app.job_models import Job
from app.security import Context


@pytest.fixture
def archive_env(tmp_path, monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    db.add_all([Tenant(id='team', name='经纪团队'),
                User(id='broker', name='经纪人', email='archive@example.test', password_hash='unused'),
                Property(id='home', tenant_id='team', name='江景公馆', city='上海'),
                Property(id='other', tenant_id='team', name='其他楼盘', city='上海'),
                Membership(id='membership', user_id='broker', tenant_id='team', role='content',
                           project_ids=['home', 'other'])])
    source = tmp_path / 'source.png'
    Image.new('RGB', (64, 64), 'green').save(source)
    db.add(Asset(id='source', tenant_id='team', property_id='home', name='客户授权客厅.png',
        kind='image', mime_type='image/png', storage_key='source.png',
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(), byte_size=source.stat().st_size,
        rights='authorized', rights_evidence='业主授权本项目营销剪辑', origin_type='actual'))
    job = Job(id='video-job', tenant_id='team', property_id='home', user_id='broker',
        kind='content_skill', status='succeeded',
        payload={'skill_id':'native:auto-short-video', 'skill_name':'auto-short-video', 'asset_ids':['source']})
    db.add(job)
    db.flush()
    from app.native_skills import _artifact_directory, _collect_artifacts
    directory = _artifact_directory(job, create=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=64x64:rate=10:duration=0.3', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(directory / 'short-video.mp4')], check=True)
    (directory / 'caption-cues.json').write_text('{"cues":[{"text":"江景示意"}]}', encoding='utf-8')
    job.result = {'execution_engine':'housignal_native','skill_name':'auto-short-video',
                  'artifacts':_collect_artifacts(job)}
    db.commit()
    yield db, Context('broker', 'team', 'content', '经纪人', 'archive@example.test', ['home', 'other']), directory
    db.close()
    engine.dispose()


def run(env, args=None, **kwargs):
    module = importlib.import_module('app.production_archive')
    db, ctx, _ = env
    return module.execute_archive_tool(db, ctx, kwargs.pop('pid', 'home'),
        'archive_production_artifacts', args or {'job_id':'video-job'}, raw_text='请把成片归档到素材库', **kwargs)


def test_real_video_archives_with_hash_provenance_and_no_commit(archive_env, monkeypatch):
    # Missing Asset creation, copied file, or implicit commit breaks the actual library contract.
    db, _, directory = archive_env
    monkeypatch.setattr(db, 'commit', lambda: pytest.fail('archive must not commit'))
    result = run(archive_env, {'job_id':'video-job','names':['short-video.mp4'],
                             'tags':['彭先生','定向看楼'], 'description':'客户定向介绍'})
    row = db.get(Asset, result['data']['assets'][0]['id'])
    assert row.kind == 'video' and row.mime_type == 'video/mp4'
    assert row.origin_type == 'actual' and row.rights == 'authorized'
    assert row.metadata_json['source_job_id'] == 'video-job'
    assert row.metadata_json['source_artifact_name'] == 'short-video.mp4'
    assert row.metadata_json['source_asset_ids'] == ['source']
    assert row.metadata_json['tags'] == ['彭先生','定向看楼']
    assert row.metadata_json['model_processing_authorized'] is False
    archived = settings.storage_root / row.storage_key
    assert archived.read_bytes() == (directory / 'short-video.mp4').read_bytes()
    assert hashlib.sha256(archived.read_bytes()).hexdigest() == row.sha256
    assert str(archived) in db.info['created_private_files']
    assert any(s['kind']=='asset' and s['id']==row.id and s['metadata_only'] for s in result['sources'])
    identity = row.id
    db.rollback()
    assert db.get(Asset, identity) is None
    from app.worker import _cleanup_outputs
    _cleanup_outputs(db)
    assert not archived.exists() and (directory / 'short-video.mp4').exists()


def test_repeat_archive_reuses_assets_and_default_includes_document(archive_env):
    db, _, _ = archive_env
    first = run(archive_env)
    second = run(archive_env)
    assert [a['id'] for a in first['data']['assets']] == [a['id'] for a in second['data']['assets']]
    assert len(db.scalars(select(Asset)).all()) == 3
    assert {a['kind'] for a in first['data']['assets']} == {'video','document'}


@pytest.mark.parametrize('names', [['../short-video.mp4'], ['/tmp/short-video.mp4'],
                                  ['https://example.test/video.mp4'], ['not-listed.mp4']])
def test_cannot_archive_paths_urls_or_unlisted_files(archive_env, names):
    with pytest.raises((HTTPException, ValueError)):
        run(archive_env, {'job_id':'video-job','names':names})
    assert len(archive_env[0].scalars(select(Asset)).all()) == 1


@pytest.mark.parametrize('state', ['queued','running','failed','cancelled'])
def test_only_completed_jobs_can_be_archived(archive_env, state):
    archive_env[0].get(Job, 'video-job').status = state
    with pytest.raises(HTTPException):
        run(archive_env)
    assert len(archive_env[0].scalars(select(Asset)).all()) == 1


def test_job_cannot_cross_property_scope(archive_env):
    with pytest.raises(HTTPException):
        run(archive_env, pid='other')


@pytest.mark.parametrize('change', ['revoked','research_only','proof_missing','missing','corrupt'])
def test_archive_rechecks_sources_including_on_repeat(archive_env, change):
    db, _, _ = archive_env
    run(archive_env, {'job_id':'video-job','names':['short-video.mp4']})
    source = db.get(Asset, 'source')
    if change == 'revoked': source.rights = 'revoked'
    elif change == 'research_only': source.metadata_json = {'usage_scope':'research_only'}
    elif change == 'proof_missing': source.rights_evidence = ''
    elif change == 'missing': (settings.storage_root / source.storage_key).unlink()
    else: (settings.storage_root / source.storage_key).write_bytes(b'corrupt')
    with pytest.raises(HTTPException):
        run(archive_env, {'job_id':'video-job','names':['short-video.mp4']})


@pytest.mark.parametrize('change', ['missing','hash_changed','bad_route','symlink','fake_media'])
def test_rejects_missing_changed_or_forged_artifacts(archive_env, change):
    db, _, directory = archive_env
    job = db.get(Job, 'video-job')
    target = directory / 'short-video.mp4'
    if change == 'missing': target.unlink()
    elif change == 'hash_changed': target.write_bytes(b'changed')
    elif change == 'bad_route':
        result = dict(job.result)
        result['artifacts'] = [{**a,'url':'/api/projects/other/operations/skills/artifacts/video-job/'+a['name']}
                               for a in result['artifacts']]
        job.result = result
    elif change == 'symlink':
        target.unlink(); target.symlink_to(settings.storage_root / 'source.png')
    else:
        target.write_bytes(b'\x00\x00\x00\x18ftypisom'+b'\x00'*64)
        from app.native_skills import _collect_artifacts
        job.result = {**job.result,'artifacts':_collect_artifacts(job)}
    with pytest.raises((HTTPException, ValueError)):
        run(archive_env, {'job_id':'video-job','names':['short-video.mp4']})
    assert len(db.scalars(select(Asset)).all()) == 1


@pytest.mark.parametrize(('project_test','source_origin','skill_name','expected'), [
    (True, 'actual', 'auto-short-video', 'test'),
    (False, 'test', 'auto-short-video', 'template'),
    (False, 'actual', 'ai-video-gen', 'ai_generated'),
    (False, 'actual', 'tts-voiceover', 'ai_generated'),
])
def test_synthetic_sources_and_model_outputs_never_become_actual(archive_env, project_test, source_origin, skill_name, expected):
    db, _, _ = archive_env
    db.get(Property, 'home').is_test = project_test
    db.get(Asset, 'source').origin_type = source_origin
    job = db.get(Job, 'video-job')
    job.payload = {**job.payload,'skill_name':skill_name}
    result = run(archive_env, {'job_id':'video-job','names':['short-video.mp4']})
    assert db.get(Asset, result['data']['assets'][0]['id']).origin_type == expected


def test_unknown_lead_rejected_before_any_copy(archive_env):
    with pytest.raises(HTTPException):
        run(archive_env, {'job_id':'video-job','lead_id':'foreign-lead'})
    assert len(archive_env[0].scalars(select(Asset)).all()) == 1


def test_archive_requires_explicit_user_instruction(archive_env):
    module = importlib.import_module('app.production_archive')
    db, ctx, _ = archive_env
    with pytest.raises(HTTPException):
        module.execute_archive_tool(db, ctx, 'home', 'archive_production_artifacts',
                                    {'job_id':'video-job'}, raw_text='查询有什么能力')


def test_invalid_later_artifact_cannot_leave_a_partial_asset_record(archive_env):
    db, _, directory = archive_env
    (directory / 'caption-cues.json').write_bytes(b'not-json')
    from app.native_skills import _collect_artifacts
    job = db.get(Job, 'video-job')
    job.result = {**job.result, 'artifacts':_collect_artifacts(job)}
    with pytest.raises(HTTPException):
        run(archive_env, {'job_id':'video-job','names':['short-video.mp4','caption-cues.json']})
    assert len(db.scalars(select(Asset)).all()) == 1
    assert not list((settings.storage_root / 'team' / 'home' / 'generated').glob('*'))


def test_unknown_synthetic_operation_never_claims_actual_capture(archive_env):
    db, _, _ = archive_env
    job = db.get(Job, 'video-job')
    job.payload = {**job.payload, 'skill_name':'xhs-note-creator'}
    result = run(archive_env, {'job_id':'video-job','names':['short-video.mp4']})
    assert db.get(Asset, result['data']['assets'][0]['id']).origin_type != 'actual'


def test_archive_rejects_readonly_membership(archive_env):
    db, ctx, _ = archive_env
    from dataclasses import replace
    readonly = replace(ctx, role='viewer')
    module = importlib.import_module('app.production_archive')
    with pytest.raises(HTTPException) as error:
        module.execute_archive_tool(db, readonly, 'home', 'archive_production_artifacts',
                                    {'job_id':'video-job'}, raw_text='归档成片')
    assert error.value.status_code == 403


def test_native_video_worker_output_archives_and_serves_from_asset_route(archive_env):
    # Exercises the missing integration: the native worker creates a real MP4,
    # then the archived file is readable through the ordinary library route.
    db, ctx, _ = archive_env
    job = Job(id='native-chain-job', tenant_id='team', property_id='home', user_id='broker',
        kind='content_skill', status='running', payload={
            'skill_id':'native:auto-short-video', 'skill_name':'auto-short-video',
            'asset_ids':['source'], 'input':'依据客户需求制作空间介绍',
            'parameters':{'size':'64x96','transition':'none','storyboard':[
                {'asset_index':0,'duration':0.4,'caption':'江景空间介绍'}]}})
    db.add(job)
    db.flush()
    from app.native_skills import handle_content_skill
    job.result = handle_content_skill(db, job)
    job.status = 'succeeded'
    db.flush()
    result = run(archive_env, {'job_id':job.id,'names':['short-video.mp4']})
    asset_id = result['data']['assets'][0]['id']
    from app.core import asset_file
    response = asset_file('home', asset_id, ctx=ctx, db=db)
    assert response.media_type == 'video/mp4'
    archived = Path(response.path)
    assert archived.is_file() and archived.stat().st_size > 100
    decoded = subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(archived),'-f','null','-'],
                             capture_output=True)
    assert decoded.returncode == 0, decoded.stderr.decode()
