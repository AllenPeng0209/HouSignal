"""Native skill HTTP and durable execution use only project-owned resources."""
import hashlib
import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.core_models import Asset, Membership, Property, Tenant, User
from app.db import Base, get_db
from app.job_models import Job
from app.security import Context, get_context


@pytest.fixture
def native_client(tmp_path, monkeypatch):
    module = None
    if importlib.util.find_spec('app.native_skills'):
        from app import native_skills as module
    import app.operations_models
    import app.agent_models
    engine = create_engine(f'sqlite:///{tmp_path / "native.db"}', connect_args={'check_same_thread': False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    storage = tmp_path / 'storage'
    source = storage / 't/p/image.png'
    source.parent.mkdir(parents=True)
    Image.new('RGB', (120, 80), 'red').save(source)
    with factory() as db:
        db.add_all([Tenant(id='t', name='测试团队'), Tenant(id='other', name='其他团队'),
                    User(id='u', email='content@example.org', password_hash='unused', name='编辑')])
        db.flush()
        db.add_all([Membership(user_id='u', tenant_id='t', role='content'),
                    Property(id='p', tenant_id='t', name='项目', city='上海'),
                    Property(id='p2', tenant_id='t', name='另一项目', city='上海')])
        db.flush()
        db.add(Asset(id='image', tenant_id='t', property_id='p', name='image.png', kind='image',
                     storage_key='t/p/image.png', byte_size=source.stat().st_size,
                     sha256=hashlib.sha256(source.read_bytes()).hexdigest(), mime_type='image/png',
                     rights='authorized', metadata_json={'model_processing_authorized': False}))
        db.commit()
    ctx = Context('u', 't', 'content', '编辑', 'content@example.org', None)
    current = {'ctx': ctx}
    app = FastAPI()
    if module:
        app.include_router(module.router)
    def database():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: current['ctx']
    monkeypatch.setattr(settings, 'storage_root', storage)
    with TestClient(app) as client:
        yield client, factory, current, module, source
    engine.dispose()


def test_native_catalog_does_not_offer_external_service_or_source_copy(native_client):
    client, _, _, _, _ = native_client
    result = client.get('/api/projects/p/operations/skills')
    assert result.status_code == 200
    rows = result.json()
    assert len({row['skill_name'] for row in rows}) == 114
    assert all(row['execution_engine'].startswith('housignal_') for row in rows)
    assert all(not row.get('easel_url') for row in rows)
    assert any(row['id'] == 'image-editing' or row['skill_name'] == 'image-editing' for row in rows)


def test_native_processing_rejects_foreign_project_and_revoked_assets(native_client):
    client, factory, current, _, _ = native_client
    base = '/api/projects/p/operations/skills/native:image-editing/run'
    current['ctx'] = replace(current['ctx'], tenant_id='other')
    assert client.post(base, json={'input': '', 'asset_ids': ['image']}).status_code == 404
    current['ctx'] = replace(current['ctx'], tenant_id='t')
    with factory() as db:
        db.get(Asset, 'image').rights = 'revoked'
        db.commit()
    assert client.post(base, json={'input': '', 'asset_ids': ['image']}).status_code == 409
    with factory() as db:
        assert not db.scalars(select(Job)).all()


def test_native_image_processing_enqueues_and_generates_a_real_project_file(native_client):
    client, factory, _, module, source = native_client
    result = client.post('/api/projects/p/operations/skills/native:image-editing/run',
                         json={'input': '', 'asset_ids': ['image'],
                               'parameters': {'op': 'resize', 'width': 60, 'height': 40}})
    assert result.status_code == 200, result.text
    with factory() as db:
        job = db.get(Job, result.json()['job_id'])
        output = module.handle_content_skill(db, job)
        assert output['execution_engine'] == 'housignal_native'
        assert output['artifacts']
        job.status, job.result = 'succeeded', output
        db.commit()
    artifact = output['artifacts'][0]
    download = client.get(artifact['url'])
    assert download.status_code == 200
    import io
    with Image.open(io.BytesIO(download.content)) as image:
        assert image.size == (60, 40)
    assert source.is_file()


def test_registered_voices_are_scoped_and_follow_current_sample_authorization(native_client):
    _, factory, current, module, _ = native_client
    with factory() as db:
        asset = db.get(Asset, 'image')
        asset.kind = 'audio'
        asset.metadata_json = {'model_processing_authorized': True}
        for identifier, project_id, voice in [('voice-ours', 'p', 'voice_current'),
                                               ('voice-other', 'p2', 'voice_foreign')]:
            db.add(Job(id=identifier, tenant_id='t', property_id=project_id, user_id='u',
                       kind='content_skill', status='succeeded',
                       payload={'skill_name': 'voice-clone', 'asset_ids': ['image'], 'parameters': {'op': 'enroll'}},
                       result={'native_data': {'voice_id': voice}}))
        db.commit()
        voices = getattr(module, '_registered_voices', lambda *args: [])(db, current['ctx'], 'p')
        assert voices == ['voice_current']
        asset.metadata_json = {'model_processing_authorized': False}
        db.commit()
        assert module._registered_voices(db, current['ctx'], 'p') == []


def test_preflight_resolves_supplied_timestamps_without_creating_or_running_a_task(native_client, monkeypatch):
    client, factory, _, _, _ = native_client
    from app import provider
    def forbidden_model_call(*args, **kwargs):
        pytest.fail('A parameter preflight must not invoke a model')
    monkeypatch.setattr(provider, 'chat', forbidden_model_call)
    segments = [{'start': 0, 'end': 1.5, 'text': '真实输入的字幕时间段'}]
    result = client.post('/api/projects/p/operations/skills/video-subtitles/preflight',
                         json={'parameters': {'format': 'srt', 'segments': segments}})
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'ready'
    assert result.json()['input_schema']['requires_model_authorization'] is False
    assert result.json()['parameters']['segments'] == segments
    with factory() as db:
        assert db.scalars(select(Job)).all() == []


def test_preflight_rechecks_project_scope_and_selected_asset_grants(native_client):
    client, factory, current, _, _ = native_client
    url = '/api/projects/p/operations/skills/native:image-editing/preflight'
    current['ctx'] = replace(current['ctx'], tenant_id='other')
    assert client.post(url, json={'asset_ids': ['image']}).status_code == 404
    current['ctx'] = replace(current['ctx'], tenant_id='t')
    with factory() as db:
        db.get(Asset, 'image').rights = 'revoked'
        db.commit()
    assert client.post(url, json={'asset_ids': ['image']}).status_code == 409
    assert client.post(url, json={'parameters': {'private_command': 'unexpected'}}).status_code == 422


def test_asset_model_permission_can_be_granted_without_automatically_running_extraction(native_client):
    client, factory, current, _, _ = native_client
    from app import core
    client.app.include_router(core.router)
    current['ctx'] = replace(current['ctx'], role='owner')
    result = client.post('/api/projects/p/assets/image/rights',
                         json={'rights': 'authorized', 'evidence': '自制验收素材，仅供项目处理',
                               'model_processing_authorized': True, 'enqueue_extraction': False})
    assert result.status_code == 200, result.text
    assert result.json()['metadata_json']['model_processing_authorized'] is True
    with factory() as db:
        assert db.scalars(select(Job)).all() == []


def test_asset_model_permission_keeps_default_extraction_behavior(native_client):
    client, factory, current, _, _ = native_client
    from app import core
    client.app.include_router(core.router)
    current['ctx'] = replace(current['ctx'], role='owner')
    result = client.post('/api/projects/p/assets/image/rights',
                         json={'rights': 'authorized', 'evidence': '自制验收素材',
                               'model_processing_authorized': True})
    assert result.status_code == 200, result.text
    with factory() as db:
        job = db.scalars(select(Job)).one()
        assert job.kind == 'extract_asset' and job.payload['asset_id'] == 'image'


def test_failed_text_quality_keeps_private_safe_metrics_for_actual_attempts(native_client, monkeypatch):
    client, factory, _, module, _ = native_client
    from app import provider
    monkeypatch.setattr(settings,'native_text_backend','dashscope')
    monkeypatch.setattr(settings,'dashscope_api_key','test-key')
    with factory() as db:
        db.get(Property,'p').is_test=True
        db.commit()
    monkeypatch.setattr(provider,'chat',lambda *args,**kwargs:{
        'model':'qwen-fixture','choices':[{'message':{'content':'所有内容基于真实拍摄与现场记录。'*5+'累计播放8000。 secret=private-value'},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':3,'completion_tokens':8}})
    response=client.post('/api/projects/p/operations/skills/native:copywriting/run',json={'input':'写约300字正文，只使用已核验资料。'})
    assert response.status_code==200,response.text
    with factory() as db:
        job=db.get(Job,response.json()['job_id'])
        with pytest.raises(Exception):
            module.handle_content_skill(db,job)
        directory=settings.storage_root/'t'/'p'/'skill_quality'/job.id
        records=list(directory.glob('attempt-*.json'))
        assert records, 'Rejected real attempts need private quality evidence'
        import json
        record=json.loads(records[0].read_text())
        assert record['passed'] is False and record['errors']
        assert record['body_metrics']['cjk_characters'] > 0
        assert any('累计播放8000' in row['fragment'] for row in record['issue_fragments']), 'A different factual violation must not disappear behind repeated photography matches'
        assert 'private-value' not in records[0].read_text()
        assert records[0].stat().st_mode & 0o777 == 0o600
        assert not list((settings.storage_root/'t'/'p'/'skill_outputs'/job.id).glob('attempt-*.json'))


def test_partial_story_diagnostics_remain_bounded_redacted_and_separate_from_final_metrics(native_client):
    import json
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='partial-story', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    module._record_quality_check(job, {'attempt':2,'passed':False,'skill_name':'novel-writer',
        'generation_mode':'novel_insert','body_metrics':{'cjk_characters':1800},
        'raw_body_metrics':{'cjk_characters':300,'invalid':-1,'boolean':True},
        'raw_safe_excerpt':'token=PRIVATE_RAW /private/PRIVATE_RAW_TEXT '+'原'*900})
    path=settings.storage_root/'t'/'p'/'skill_quality'/job.id/'attempt-02.json'
    result=json.loads(path.read_text())
    assert result['generation_mode']=='novel_insert'
    assert result['body_metrics']['cjk_characters']==1800
    assert result['raw_body_metrics']=={'cjk_characters':300}
    assert len(result['raw_safe_excerpt'])<=500 and 'PRIVATE_RAW' not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list((settings.storage_root/'t'/'p'/'skill_outputs'/job.id).iterdir())


def test_novel_private_evidence_retains_complete_sanitized_candidates_and_exact_duplicate_positions(native_client):
    import json
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='novel-evidence', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    fragment = '这一段测试故事需要逐字核对。' * 60
    candidate = '假小说正文。' * 1600 + ' secret=FIXTURE_PRIVATE_VALUE /private/FIXTURE_PRIVATE_PATH'
    raw = '假补写正文。' * 1200 + ' token=FIXTURE_RAW_VALUE'
    issue = {'error': '补写逐字复写原章段落', 'fragment': fragment,
             'duplicate_kind': 'retained_base_paragraph', 'position_unit': 'python_codepoint',
             'position_scope': 'retained_base_and_raw_insert_before_redaction',
             'base_start': 42, 'base_end': 42 + len(fragment), 'insert_start': 10,
             'insert_end': 10 + len(fragment), 'repeated_insert_start': 0,
             'repeated_insert_end': 0, 'full_fragment_characters': len(fragment), 'truncated': False}
    module._record_quality_check(job, {'skill_name': 'novel-writer', 'attempt': 1, 'passed': False,
        'fiction_candidate': candidate, 'raw_fiction_candidate': raw, 'issue_fragments': [issue] * 10})
    path = settings.storage_root/'t'/'p'/'skill_quality'/job.id/'attempt-01.json'
    saved = json.loads(path.read_text())
    assert saved['fiction_candidate'].startswith('假小说正文。' * 1600)
    assert saved['raw_fiction_candidate'].startswith('假补写正文。' * 1200)
    assert 'FIXTURE_PRIVATE' not in path.read_text() and 'FIXTURE_RAW_VALUE' not in path.read_text()
    assert len(saved['issue_fragments']) == 8
    assert saved['issue_fragments'][0] == issue
    assert path.stat().st_mode & 0o777 == 0o600
    assert list((settings.storage_root/'t'/'p'/'skill_outputs'/job.id).iterdir()) == []


def test_novel_private_evidence_bounds_full_candidates_and_marks_clipped_duplicate_fragment(native_client):
    import json
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='novel-evidence-bounded', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    module._record_quality_check(job, {'skill_name': 'novel-writer', 'attempt': 1,
        'fiction_candidate': '甲' * 31000, 'raw_fiction_candidate': '乙' * 31000,
        'issue_fragments': [{'error': '补写重复', 'fragment': '丙' * 1300,
            'duplicate_kind': 'repeated_insert_paragraph', 'position_unit': 'python_codepoint',
            'insert_start': 1, 'insert_end': 1301, 'repeated_insert_start': 1500,
            'repeated_insert_end': 2800, 'full_fragment_characters': 1300, 'truncated': False}]})
    path = settings.storage_root/'t'/'p'/'skill_quality'/job.id/'attempt-01.json'
    saved = json.loads(path.read_text())
    assert saved['fiction_candidate'] == '甲' * 30000
    assert saved['raw_fiction_candidate'] == '乙' * 30000
    issue = saved['issue_fragments'][0]
    assert issue['fragment'] == '丙' * 1000
    assert issue['truncated'] is True and issue['full_fragment_characters'] == 1300
    assert issue['insert_start'] == 1 and issue['insert_end'] == 1301
    assert issue['repeated_insert_start'] == 1500 and issue['repeated_insert_end'] == 2800


def test_non_novel_private_evidence_does_not_store_full_candidate_fields(native_client):
    import json
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='copy-private-evidence', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    module._record_quality_check(job, {'skill_name': 'copywriting', 'attempt': 1,
        'fiction_candidate': '不应额外保存整篇营销稿', 'raw_fiction_candidate': '不应额外保存原始素材',
        'issue_fragments': [{'error': '测试提示', 'fragment': '测试节选',
                             'position_scope': 'retained_base_and_raw_insert_before_redaction',
                             'position_unit': 'python_codepoint', 'insert_start': 12, 'insert_end': 16}]})
    path = settings.storage_root/'t'/'p'/'skill_quality'/job.id/'attempt-01.json'
    saved = json.loads(path.read_text())
    assert 'fiction_candidate' not in saved and 'raw_fiction_candidate' not in saved
    assert '整篇营销稿' not in path.read_text() and '原始素材' not in path.read_text()
    assert saved['issue_fragments'] == [{'error': '测试提示', 'fragment': '测试节选'}]


def test_existing_private_quality_receipt_permissions_are_tightened_before_rewrite(native_client):
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='old-private-mode', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    directory = settings.storage_root/'t'/'p'/'skill_quality'/job.id
    directory.mkdir(parents=True)
    path = directory/'attempt-01.json'
    path.write_text('old fixture')
    path.chmod(0o644)
    module._record_quality_check(job, {'skill_name': 'novel-writer', 'attempt': 1})
    assert path.stat().st_mode & 0o777 == 0o600


def test_private_quality_receipt_rejects_existing_hardlink_without_changing_its_target(native_client):
    from types import SimpleNamespace
    _, _, _, module, source = native_client
    job = SimpleNamespace(id='hardlinked-evidence', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    directory = settings.storage_root/'t'/'p'/'skill_quality'/job.id
    directory.mkdir(parents=True)
    target = source.parents[3]/'outside-original.txt'
    target.write_text('untouched hardlink fixture')
    target.chmod(0o644)
    (directory/'attempt-01.json').hardlink_to(target)
    with pytest.raises(ValueError):
        module._record_quality_check(job, {'skill_name': 'novel-writer', 'attempt': 1})
    assert target.read_text() == 'untouched hardlink fixture'
    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize('linked_segment', ['quality_directory', 'job_directory', 'receipt'])
def test_private_quality_receipt_rejects_symlinks_without_creating_or_overwriting_outside_files(native_client, linked_segment):
    from types import SimpleNamespace
    _, _, _, module, source = native_client
    job = SimpleNamespace(id='linked-evidence', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    outside = source.parents[3]/'outside-quality'
    outside.mkdir()
    quality = settings.storage_root/'t'/'p'/'skill_quality'
    if linked_segment == 'quality_directory':
        quality.symlink_to(outside, target_is_directory=True)
    elif linked_segment == 'job_directory':
        quality.mkdir()
        (quality/job.id).symlink_to(outside, target_is_directory=True)
    else:
        directory = quality/job.id
        directory.mkdir(parents=True)
        target = outside/'original.txt'
        target.write_text('untouched fixture')
        (directory/'attempt-01.json').symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        module._record_quality_check(job, {'skill_name': 'novel-writer', 'attempt': 1})
    assert {path.name for path in outside.iterdir()} == ({'original.txt'} if linked_segment == 'receipt' else set())
    if linked_segment == 'receipt':
        assert target.read_text() == 'untouched fixture'


@pytest.mark.parametrize('change', ['cancel', 'membership', 'project', 'fact'])
def test_private_quality_observer_does_not_write_after_cancellation_access_revocation_or_snapshot_change(native_client, monkeypatch, change):
    from fastapi import HTTPException
    from app.content_skills import text
    from app.core_models import Fact
    from app.worker import LostLease
    client, factory, _, module, _ = native_client
    monkeypatch.setattr(settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    with factory() as db:
        db.get(Property, 'p').is_test = True
        db.add(Fact(id='quality-fact', tenant_id='t', property_id='p', semantic_key='area',
                    label='面积', value='329', unit='㎡', source='测试测绘文件', status='verified'))
        db.commit()
    response = client.post('/api/projects/p/operations/skills/novel-writing/run',
                           json={'input': '写一段全新小说正文。'})
    assert response.status_code == 200, response.text
    cancelled = [False]
    def rejected_candidate(db, job, **kwargs):
        if change == 'cancel':
            cancelled[0] = True
        else:
            with factory() as changed:
                if change == 'membership':
                    changed.scalar(select(Membership).where(Membership.user_id == 'u', Membership.tenant_id == 't')).active = False
                elif change == 'project':
                    changed.get(Property, 'p').audience = '已变化的测试受众'
                else:
                    changed.get(Fact, 'quality-fact').value = '330'
                changed.commit()
        db.info['text_quality_observer']({'skill_name': 'novel-writer', 'attempt': 1, 'passed': False,
                                        'fiction_candidate': '不应保存的失败测试正文'})
        return {'response': '不应交付的失败测试正文'}
    monkeypatch.setattr(text, 'execute_text', rejected_candidate)
    with factory() as db:
        job = db.get(Job, response.json()['job_id'])
        db.info['job_cancelled'] = lambda: cancelled[0]
        with pytest.raises(LostLease if change == 'cancel' else HTTPException):
            module.handle_content_skill(db, job)
        assert not list((settings.storage_root/'t'/'p'/'skill_quality'/job.id).glob('*.json'))
        assert not list((settings.storage_root/'t'/'p'/'skill_outputs'/job.id).iterdir())


def test_failed_novel_candidate_is_private_and_absent_from_public_artifacts(native_client, monkeypatch):
    import json
    from app import provider
    client, factory, _, module, _ = native_client
    monkeypatch.setattr(settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    with factory() as db:
        db.get(Property, 'p').is_test = True
        db.commit()
    monkeypatch.setattr(provider, 'chat', lambda *args, **kwargs: {
        'model': 'qwen-fixture', 'choices': [{'message': {'content': '失败测试小说正文。'}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 3, 'completion_tokens': 8}})
    response = client.post('/api/projects/p/operations/skills/novel-writing/run',
                           json={'input': '第一章正文100至120个中文字符。'})
    assert response.status_code == 200, response.text
    with factory() as db:
        job = db.get(Job, response.json()['job_id'])
        with pytest.raises(provider.ProviderError):
            module.handle_content_skill(db, job)
        quality = settings.storage_root/'t'/'p'/'skill_quality'/job.id
        assert list(quality.glob('attempt-*.json'))
        saved = json.loads((quality/'attempt-01.json').read_text())
        assert saved['fiction_candidate'] == '失败测试小说正文。'
        assert saved['raw_fiction_candidate'] == '失败测试小说正文。'
        assert module._collect_artifacts(job) == []
        assert client.get(f'/api/projects/p/operations/skills/artifacts/{job.id}/attempt-01.json').status_code == 404


@pytest.mark.parametrize('generation_mode', ['novel_section', 'novel_whole'])
def test_private_novel_section_evidence_preserves_phase_contract_states_and_unapproved_review(native_client, generation_mode):
    import json
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='section-evidence', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    before = {'location': '测试旧站入口', 'time': '第二天清晨', 'viewpoint': '女主有限视角',
              'props': {'测试钥匙': '女主外套内袋'}}
    after = {'location': '测试旧站候车室', 'time': '第二天清晨稍后', 'viewpoint': '女主有限视角',
             'props': {'测试钥匙': '遗落在入口地面'}}
    contract = {'minimum': 450, 'maximum': 550, 'metric': 'cjk_characters'}
    event = {'skill_name': 'novel-writer', 'attempt': 2, 'passed': True, 'generation_mode': generation_mode,
             'section_index': 2, 'section_attempt': 1, 'section_contract': contract,
             'section_state_before': before, 'section_state_after': after,
             'narrative_review': {'performed': True, 'issue_count': 0, 'commercial_approval': True,
                                  'raw_model_review': '不应扩展保存的测试审查原文'}}
    module._record_quality_check(job, event)
    path = settings.storage_root/'t'/'p'/'skill_quality'/job.id/'attempt-02.json'
    saved = json.loads(path.read_text())
    assert saved['generation_mode'] == generation_mode and saved['passed'] is True
    assert saved['section_index'] == 2 and saved['section_attempt'] == 1
    assert saved['section_contract'] == contract
    assert saved['section_state_before'] == before and saved['section_state_after'] == after
    assert saved['narrative_review'] == {'performed': True, 'issue_count': 0, 'commercial_approval': False}
    assert 'raw_model_review' not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


def test_private_novel_section_states_are_bounded_sanitized_and_limited_to_known_fields(native_client):
    import json
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='section-state-bounds', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    state = {'location': 'token=SECTION_FIXTURE_TOKEN ' + '测试地点' * 80,
             'time': '/private/SECTION_FIXTURE_PATH ' + '测试时刻' * 80,
             'viewpoint': 'secret=SECTION_FIXTURE_SECRET ' + '有限视角' * 80,
             'props': {'测试道具' + str(index) + '甲' * 180: 'password=SECTION_PROP_FIXTURE ' + '持有状态' * 80
                       for index in range(10)},
             'private_notes': '不应保存的测试内部注记'}
    module._record_quality_check(job, {'skill_name': 'novel-writer', 'attempt': 1,
        'generation_mode': 'novel_section', 'section_state_before': state, 'section_state_after': state})
    path = settings.storage_root/'t'/'p'/'skill_quality'/job.id/'attempt-01.json'
    saved = json.loads(path.read_text())
    for key in ('section_state_before', 'section_state_after'):
        result = saved[key]
        assert set(result) == {'location', 'time', 'viewpoint', 'props'}
        assert all(len(result[field]) <= 160 for field in ('location', 'time', 'viewpoint'))
        assert len(result['props']) == 8
        assert all(len(name) <= 160 and len(holder) <= 160 for name, holder in result['props'].items())
    assert 'SECTION_FIXTURE' not in path.read_text() and 'SECTION_PROP_FIXTURE' not in path.read_text()
    assert 'private_notes' not in path.read_text()


@pytest.mark.parametrize('invalid', [
    {'section_index': True, 'section_attempt': True},
    {'section_index': 0, 'section_attempt': 0},
    {'section_index': 5, 'section_attempt': 3},
    {'section_contract': {'minimum': 550, 'maximum': 450, 'metric': 'cjk_characters'}},
    {'section_contract': {'minimum': True, 'maximum': 550, 'metric': 'cjk_characters'}},
    {'section_contract': {'minimum': 1, 'maximum': 30001, 'metric': 'cjk_characters'}},
    {'section_contract': {'minimum': 450, 'maximum': 550, 'metric': 'characters_without_whitespace'}},
    {'narrative_review': {'performed': True, 'issue_count': True, 'commercial_approval': True}},
    {'narrative_review': {'performed': True, 'issue_count': 7, 'commercial_approval': True}},
    {'section_state_before': 'not a state', 'section_state_after': {'location': {'nested': 'not a string'}}},
])
def test_private_novel_section_evidence_omits_invalid_metadata(native_client, invalid):
    import json
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='invalid-section-metadata', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    module._record_quality_check(job, {'skill_name': 'novel-writer', 'attempt': 1,
                                      'generation_mode': 'novel_section', **invalid})
    path = settings.storage_root/'t'/'p'/'skill_quality'/job.id/'attempt-01.json'
    saved = json.loads(path.read_text())
    assert all(key not in saved for key in invalid)


def test_non_novel_private_evidence_does_not_gain_section_phase_or_narrative_metadata(native_client):
    import json
    from types import SimpleNamespace
    _, _, _, module, _ = native_client
    job = SimpleNamespace(id='copy-section-fields', tenant_id='t', property_id='p')
    module._artifact_directory(job, create=True)
    fields = {'section_index': 1, 'section_attempt': 1,
              'section_contract': {'minimum': 450, 'maximum': 550, 'metric': 'cjk_characters'},
              'section_state_before': {'location': '测试入口', 'time': '清晨', 'viewpoint': '女主', 'props': {}},
              'section_state_after': {'location': '测试候车室', 'time': '清晨', 'viewpoint': '女主', 'props': {}},
              'narrative_review': {'performed': True, 'issue_count': 0, 'commercial_approval': False}}
    module._record_quality_check(job, {'skill_name': 'copywriting', 'attempt': 1,
                                      'generation_mode': 'novel_section', **fields})
    path = settings.storage_root/'t'/'p'/'skill_quality'/job.id/'attempt-01.json'
    saved = json.loads(path.read_text())
    assert saved['generation_mode'] == 'full'
    assert all(key not in saved for key in fields)
