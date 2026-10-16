"""Native artifact archival accepts known legacy MIME labels without weakening integrity."""
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core_models import Asset
from app.job_models import Job
from app.production_archive import execute_archive_tool
from test_business import scenario


SRT = '1\n00:00:00,000 --> 00:00:02,500\n先看一家人的生活动线。\n\n'.encode('utf-8')


def task_receipt(scenario, tmp_path, *, raw=SRT, name='subtitles.srt', mime='text/plain',
                 status='succeeded', engine='housignal_native'):
    db, _, project, *_ = scenario
    directory = tmp_path/'tenant'/project.id/'skill_outputs'/'tts-job'
    directory.mkdir(parents=True)
    (directory/name).write_bytes(raw)
    job = Job(id='tts-job', tenant_id='tenant', property_id=project.id, user_id='admin',
              kind='content_skill', status=status,
              payload={'skill_name': 'tts-voiceover', 'asset_ids': []},
              result={'execution_engine': engine, 'artifacts': [{
                  'name': name, 'mime_type': mime, 'byte_size': len(raw),
                  'sha256': hashlib.sha256(raw).hexdigest(),
                  'url': f'/api/projects/{project.id}/operations/skills/artifacts/tts-job/{name}'}]})
    db.add(job)
    db.commit()
    return job


def archive(scenario, **args):
    db, ctx, project, *_ = scenario
    return execute_archive_tool(db, ctx, project.id, 'archive_production_artifacts',
                                {'job_id': 'tts-job', **args}, raw_text='将本次制作产物归档到当前项目素材库')


@pytest.mark.parametrize('raw', [SRT, b'\xef\xbb\xbf' + SRT], ids=['utf8', 'utf8-bom'])
def test_tts_srt_plain_text_receipt_archives_as_canonical_subrip(scenario, tmp_path, raw):
    db = scenario[0]
    task_receipt(scenario, tmp_path, raw=raw)
    result = archive(scenario, names=['subtitles.srt'])
    db.commit()
    asset = db.get(Asset, result['data']['archived_asset_ids'][0])
    assert asset.kind == 'document'
    assert asset.mime_type == 'application/x-subrip'
    assert asset.sha256 == hashlib.sha256(raw).hexdigest()
    assert asset.byte_size == len(raw)
    assert (tmp_path/asset.storage_key).read_bytes() == raw
    assert asset.metadata_json['source_job_id'] == 'tts-job'
    assert asset.metadata_json['source_artifact_name'] == 'subtitles.srt'
    assert asset.metadata_json['source_sha256'] == asset.sha256
    assert asset.metadata_json['model_processing_authorized'] is False
    assert asset.metadata_json['review_required'] is True
    assert asset.metadata_json['publication_state'] == 'not_published'
    assert result['sources'][0]['id'] == 'tts-job'


@pytest.mark.parametrize('raw', [b'\xff\xfe\xfd', SRT + b'\x00'])
def test_srt_legacy_mime_never_skips_utf8_or_nul_validation(scenario, tmp_path, raw):
    task_receipt(scenario, tmp_path, raw=raw)
    with pytest.raises(HTTPException) as error:
        archive(scenario)
    assert error.value.status_code == 422
    assert scenario[0].scalar(select(func.count()).select_from(Asset)) == 1
    assert not list(tmp_path.glob('tenant/p/generated/*'))


@pytest.mark.parametrize('name,mime,raw,status', [
    ('subtitles.srt', 'application/x-msdownload', SRT, 409),
    ('subtitles.srt', 'application/octet-stream', SRT, 409),
    ('report.json', 'text/plain', b'{"case": "original"}', 409),
    ('program.exe', 'text/plain', SRT, 404),
])
def test_legacy_plain_text_mime_is_only_accepted_for_known_subtitle_suffix(scenario, tmp_path, name, mime, raw, status):
    task_receipt(scenario, tmp_path, name=name, mime=mime, raw=raw)
    with pytest.raises(HTTPException) as error:
        archive(scenario, names=[name])
    assert error.value.status_code == status
    assert scenario[0].scalar(select(func.count()).select_from(Asset)) == 1
    assert not list(tmp_path.glob('tenant/p/generated/*'))


@pytest.mark.parametrize('change,status', [('tampered_sha', 409), ('failed_task', 404), ('fake_engine', 409)])
def test_legacy_srt_still_requires_completed_native_receipt_and_matching_sha(scenario, tmp_path, change, status):
    job = task_receipt(scenario, tmp_path,
                       status='failed' if change == 'failed_task' else 'succeeded',
                       engine='untrusted' if change == 'fake_engine' else 'housignal_native')
    if change == 'tampered_sha':
        (tmp_path/'tenant'/'p'/'skill_outputs'/job.id/'subtitles.srt').write_bytes(SRT + b'altered')
    with pytest.raises(HTTPException) as error:
        archive(scenario)
    assert error.value.status_code == status
    assert scenario[0].scalar(select(func.count()).select_from(Asset)) == 1
    assert not list(tmp_path.glob('tenant/p/generated/*'))
