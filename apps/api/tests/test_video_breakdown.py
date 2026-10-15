"""Video reference notes stay distinct from verified target-project facts."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.core_models import Fact, Property, Tenant, User
from app.operations_models import ContentPlan
from app.security import Context, get_context


ROOT = '/api/projects/p/operations/discovery/breakdown'
SOURCE = 'https://www.douyin.com/share/video/7390000000000000001?share_token=private#share'


@pytest.fixture
def breakdown_client():
    app = FastAPI()
    from app.discovery import router
    app.include_router(router)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='其他团队'),
                    User(id='u', name='编辑', email='breakdown@example.org', password_hash='unused')])
        db.flush()
        db.add_all([Property(id='p', tenant_id='t', name='当前住宅', city='苏州'),
                    Property(id='p2', tenant_id='t', name='其他住宅', city='杭州')])
        db.flush()
        db.add_all([
            Fact(id='own-area', tenant_id='t', property_id='p', semantic_key='area', label='建筑面积',
                 value='120', unit='㎡', source='核验户型图', status='verified', revision=3),
            Fact(id='own-layout', tenant_id='t', property_id='p', semantic_key='layout', label='户型',
                 value='待确认的四房', source='销售口述', status='unverified'),
            Fact(id='expired-width', tenant_id='t', property_id='p', semantic_key='width', label='客厅开间',
                 value='6', unit='米', source='旧图纸', status='verified',
                 valid_until=datetime.now(timezone.utc)-timedelta(days=1)),
            Fact(id='foreign-area', tenant_id='t', property_id='p2', semantic_key='area', label='建筑面积',
                 value='999', unit='㎡', source='别的项目', status='verified'),
        ])
        db.commit()
        ctx = Context('u', 't', 'content', '编辑', 'breakdown@example.org')
        current = {'ctx': ctx}
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_context] = lambda: current['ctx']
        with TestClient(app) as client:
            yield client, db, current, ctx
    engine.dispose()


def preview(client, **overrides):
    return client.post(ROOT, json={'source_url': SOURCE, 'title': '参考豪宅视频',
        'transcript': '[00:00-00:03] 888万元买400㎡？\n[00:03-00:10] 先看客厅',
        'observations': '[00:00-00:03] 从玄关推镜到客厅', 'topic_id': 'space', **overrides})


def test_breakdown_preserves_supplied_timing_without_claiming_to_watch_video(breakdown_client):
    client, db, _, _ = breakdown_client
    response = preview(client)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['source']['url'] == 'https://www.douyin.com/video/7390000000000000001'
    assert data['source']['watched'] is False
    assert data['source']['evidence_basis'] == 'user_supplied_notes'
    assert data['timeline'][0] == {'start_seconds': 0, 'end_seconds': 3,
        'transcript': '888万元买400㎡？', 'visual': '从玄关推镜到客厅', 'evidence_status': 'user_supplied'}
    assert data['hook']['status'] == 'user_supplied'
    assert '888万元' in data['hook']['evidence']
    assert db.scalar(select(ContentPlan)) is None


def test_target_shots_use_only_valid_selected_project_facts(breakdown_client):
    client, _, _, _ = breakdown_client
    response = preview(client, target_seconds=45)
    assert response.status_code == 200, response.text
    data = response.json()
    adaptation = data['adaptation']
    assert adaptation['eligible'] is True
    assert adaptation['fact_refs'] == [{'id': 'own-area', 'revision': 3, 'label': '建筑面积',
        'value': '120', 'unit': '㎡', 'source': '核验户型图'}]
    assert adaptation['shots'][0]['start_seconds'] == 0
    assert adaptation['shots'][-1]['end_seconds'] == 45
    script = '\n'.join(shot['voiceover'] for shot in adaptation['shots'])
    assert '120㎡' in script
    assert all(value not in script for value in ['888', '400', '999', '四房'])
    assert len(data['plan_seed']['brief']) <= 12000
    assert data['plan_seed']['channel'] == '抖音'
    assert data['source']['url'] in data['plan_seed']['brief']
    assert '未经核验' in data['plan_seed']['brief']


def test_untimed_notes_do_not_become_opening_or_invented_timestamps(breakdown_client):
    client, _, _, _ = breakdown_client
    response = preview(client, transcript='客厅很大，价格很低', observations='航拍转室内')
    assert response.status_code == 200, response.text
    data = response.json()
    assert all(row['start_seconds'] is None and row['end_seconds'] is None for row in data['timeline'])
    assert data['hook']['status'] == 'pending'
    assert data['hook']['evidence'] == ''
    assert '时间待补充' in data['report']


def test_later_notes_do_not_claim_first_three_seconds_are_known(breakdown_client):
    client, _, _, _ = breakdown_client
    data = preview(client, transcript='[00:05-00:09] 卖点介绍', observations='').json()
    assert data['hook']['status'] == 'pending'
    assert data['hook']['evidence'] == ''


def test_source_structure_retains_timeline_gaps_and_marks_opening_clues_as_hypotheses(breakdown_client):
    client, _, _, _ = breakdown_client
    response = preview(client, transcript='[00:00-00:03] 为什么同样面积，动线差这么多？\n'
        '[00:05-00:10] 看厨房入口\n临时补充，时间没记', observations='')
    assert response.status_code == 200, response.text
    data = response.json()
    structure = data['structure']
    assert structure['known_clip_count'] == 2
    assert structure['untimed_clip_count'] == 1
    assert structure['last_known_seconds'] == 10
    assert structure['gaps'] == [{'start_seconds': 3, 'end_seconds': 5}]
    assert structure['opening_evidence'][0]['transcript'] == '为什么同样面积，动线差这么多？'
    assert structure['middle_evidence'][0]['transcript'] == '看厨房入口'
    assert structure['last_recorded_evidence'][0]['end_seconds'] == 10
    assert structure['opening_hypotheses'][0]['status'] == 'hypothesis'
    assert '问题' in structure['opening_hypotheses'][0]['label']
    assert '00:03–00:05' in data['report']
    assert '完整片尾未知' in data['report']


@pytest.mark.parametrize('topic,missing', [('layout', '户型'), ('light', '客厅开间')])
def test_missing_or_expired_facts_produce_placeholders(breakdown_client, topic, missing):
    client, _, _, _ = breakdown_client
    response = preview(client, topic_id=topic)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['adaptation']['eligible'] is False
    assert data['adaptation']['fact_refs'] == []
    assert missing in data['adaptation']['missing_facts']
    assert '待补充并核验' in '\n'.join(shot['voiceover'] for shot in data['adaptation']['shots'])


@pytest.mark.parametrize('overrides', [
    {'transcript': ' ', 'observations': ''}, {'title': ' '}, {'target_seconds': 14},
    {'target_seconds': 61}, {'target_seconds': True}, {'target_seconds': '30'},
    {'topic_id': 'price'}, {'extra': 'unsupported'}, {'transcript': 'x'*10001},
    {'source_url': 'https://www.douyin.com.evil.example/video/7390000000000000001'},
    {'source_url': 'https://user:password@www.douyin.com/video/7390000000000000001'},
    {'source_url': 'http://127.0.0.1/video/7390000000000000001'},
    {'transcript': '[00:70-00:75] 错误时间'}, {'transcript': '[00:10-00:05] 倒序'},
    {'transcript': '[60:00-60:03] 过长'},
    {'transcript': '[-00:03-00:05] 负数'}, {'transcript': '[01:00:00-01:00:05] 超时'},
    {'transcript': '[00:03-abc] 错误范围'}, {'transcript': '[00:03-00:05 未闭合'},
])
def test_breakdown_rejects_unsupported_or_ambiguous_input(breakdown_client, overrides):
    client, _, _, _ = breakdown_client
    assert preview(client, **overrides).status_code == 422


def test_breakdown_enforces_role_tenant_and_project_scope(breakdown_client):
    client, _, current, ctx = breakdown_client
    current['ctx'] = replace(ctx, role='viewer')
    assert preview(client).status_code == 403
    current['ctx'] = replace(ctx, tenant_id='other')
    assert preview(client).status_code == 404
    current['ctx'] = replace(ctx, project_ids=['p2'])
    assert preview(client).status_code == 404


def test_breakdown_seed_can_be_persisted_by_existing_plan_endpoint(breakdown_client):
    client, db, _, _ = breakdown_client
    from app.operations import router
    client.app.include_router(router)
    data = preview(client).json()
    response = client.post('/api/projects/p/operations/plans', json=data['plan_seed'])
    assert response.status_code == 200, response.text
    plan = db.get(ContentPlan, response.json()['id'])
    assert plan.topic_id == 'space'
    assert '120㎡' in plan.brief
    assert '原片' in plan.brief


def test_source_question_and_contrast_change_adapted_hook_and_guidance(breakdown_client):
    client, _, _, _ = breakdown_client
    question = preview(client, transcript='[00:00-00:03] 为什么400㎡也会显挤？\n[00:03-00:12] 核对动线', observations='').json()
    contrast = preview(client, transcript='[00:00-00:03] 不是越大越好，而是看动线\n[00:03-00:12] 核对动线', observations='').json()
    assert question['adaptation']['shots'][0]['purpose'] != contrast['adaptation']['shots'][0]['purpose']
    assert question['adaptation']['shots'][0]['voiceover'] != contrast['adaptation']['shots'][0]['voiceover']
    assert question['adaptation']['shots'][1]['visual'] != contrast['adaptation']['shots'][1]['visual']
    assert question['plan_seed']['brief'] != contrast['plan_seed']['brief']
    assert '400㎡' not in question['plan_seed']['brief']
    assert '为什么400㎡也会显挤' not in question['plan_seed']['brief']


def test_reference_phase_boundaries_influence_proposed_target_timing(breakdown_client):
    client, _, _, _ = breakdown_client
    short_middle = preview(client, target_seconds=45, observations='', transcript=
        '[00:00-00:03] 怎么看户型？\n[00:03-00:08] 核对资料\n[00:08-00:30] 展示').json()
    long_middle = preview(client, target_seconds=45, observations='', transcript=
        '[00:00-00:03] 怎么看户型？\n[00:03-00:18] 核对资料\n[00:18-00:30] 展示').json()
    assert short_middle['adaptation']['shots'][1]['end_seconds'] == 12
    assert long_middle['adaptation']['shots'][1]['end_seconds'] == 27
    assert long_middle['adaptation']['shots'][0]['end_seconds'] == 3
    assert long_middle['adaptation']['shots'][-1]['end_seconds'] == 45


def test_numeric_source_anchor_is_replaced_with_current_project_number(breakdown_client):
    client, _, _, _ = breakdown_client
    data = preview(client, transcript='[00:00-00:03] 建筑面积400㎡', observations='').json()
    assert data['adaptation']['reuse_template']['id'] == 'numeric_anchor'
    assert '120㎡' in data['adaptation']['shots'][0]['voiceover']
    assert '400㎡' not in data['plan_seed']['brief']


def save_payload(client, **reference_overrides):
    reference = {'source_url': SOURCE, 'title': '参考豪宅视频', 'transcript': '[00:00-00:03] 888万元买400㎡？',
                 'observations': '[00:00-00:03] 从玄关推镜到客厅', 'target_seconds': 30,
                 'topic_id': 'space', **reference_overrides}
    seed = client.post(ROOT, json=reference).json()['plan_seed']
    return {'reference': reference, 'plan': seed, 'idempotency_key': 'aaf044b0-060b-4122-8ae8-194a1e568c11'}


def test_breakdown_save_retains_source_notes_and_opportunity_snapshot(breakdown_client):
    client, db, _, ctx = breakdown_client
    from app.operations import create_opportunity
    opportunity = create_opportunity(db, ctx, 'p', {'title': '观察来源', 'kind': 'competitor', 'platform': '抖音',
        'source_url': 'https://www.douyin.com/video/7390000000000000001', 'summary': '指标未获取'})
    db.commit()
    payload = save_payload(client)
    payload['opportunity_id'] = opportunity['id']
    response = client.post(ROOT + '/plan', json=payload)
    assert response.status_code == 200, response.text
    with Session(db.bind) as fresh:
        plan = fresh.get(ContentPlan, response.json()['id'])
        source = plan.source_snapshot
        assert source['id'] == opportunity['id']
        assert source['summary'] == '指标未获取'
        evidence = source['reference_analysis']
        assert evidence['schema_version'] == 1
        assert evidence['reference']['transcript'] == '[00:00-00:03] 888万元买400㎡？'
        assert evidence['source']['url'] == 'https://www.douyin.com/video/7390000000000000001'
        assert evidence['timeline'][0]['visual'] == '从玄关推镜到客厅'
        assert evidence['hook']['status'] == 'user_supplied'
        assert evidence['idempotency_key'] == payload['idempotency_key']
        expected_reference = payload['reference'] | {'source_url': evidence['source']['url']}
        encoded = json.dumps(expected_reference, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        assert evidence['content_sha256'] == hashlib.sha256(encoded).hexdigest()
        assert datetime.fromisoformat(evidence['observed_at']).tzinfo is not None
        assert '888万元' not in plan.brief
        assert '400㎡' not in plan.brief


def test_breakdown_save_retries_return_original_and_changed_request_conflicts(breakdown_client):
    client, db, _, _ = breakdown_client
    payload = save_payload(client)
    saved = client.post(ROOT + '/plan', json=payload)
    assert saved.status_code == 200, saved.text
    db.get(Fact, 'own-area').value = '121'
    db.commit()
    retry = client.post(ROOT + '/plan', json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json() == saved.json()
    changed = payload | {'plan': payload['plan'] | {'title': '修改后的标题'}}
    assert client.post(ROOT + '/plan', json=changed).status_code == 409
    assert len(db.scalars(select(ContentPlan)).all()) == 1


@pytest.mark.parametrize('kind', ['mismatched_topic', 'invalid_key', 'extra_plan_field', 'too_long_brief'])
def test_breakdown_save_rejects_invalid_contract_without_persistence(breakdown_client, kind):
    client, db, _, _ = breakdown_client
    payload = save_payload(client)
    if kind == 'mismatched_topic':
        payload['plan']['topic_id'] = 'layout'
    elif kind == 'invalid_key':
        payload['idempotency_key'] = 'not-a-uuid'
    elif kind == 'extra_plan_field':
        payload['plan']['source_snapshot'] = {'forged': True}
    else:
        payload['plan']['brief'] = 'x' * 12001
    assert client.post(ROOT + '/plan', json=payload).status_code == 422
    assert db.scalar(select(ContentPlan)) is None


def test_breakdown_save_enforces_scope_and_does_not_replace_existing_evidence(breakdown_client):
    client, db, current, ctx = breakdown_client
    payload = save_payload(client)
    current['ctx'] = replace(ctx, role='viewer')
    assert client.post(ROOT + '/plan', json=payload).status_code == 403
    current['ctx'] = replace(ctx, tenant_id='other')
    assert client.post(ROOT + '/plan', json=payload).status_code == 404
    current['ctx'] = replace(ctx, project_ids=['p2'])
    assert client.post(ROOT + '/plan', json=payload).status_code == 404
    current['ctx'] = ctx
    saved = client.post(ROOT + '/plan', json=payload)
    assert saved.status_code == 200, saved.text
    p2 = client.post(ROOT.replace('/p/', '/p2/') + '/plan', json=payload)
    assert p2.status_code == 200, p2.text
    assert p2.json()['id'] != saved.json()['id']
    assert len(db.scalars(select(ContentPlan)).all()) == 2


def test_breakdown_save_rejects_reference_url_mismatch_with_opportunity(breakdown_client):
    client, db, _, ctx = breakdown_client
    from app.operations import create_opportunity
    opportunity = create_opportunity(db, ctx, 'p', {'title': '另一个作品', 'kind': 'competitor', 'platform': '抖音',
        'source_url': 'https://www.douyin.com/video/7390000000000000002', 'summary': '原始记录'})
    db.commit()
    payload = save_payload(client) | {'opportunity_id': opportunity['id']}
    assert client.post(ROOT + '/plan', json=payload).status_code == 409
    assert db.scalar(select(ContentPlan)) is None


def test_new_save_key_cannot_attach_evidence_to_an_existing_opportunity_plan(breakdown_client):
    client, db, _, ctx = breakdown_client
    from app.operations import create_opportunity, create_plan
    opportunity = create_opportunity(db, ctx, 'p', {'title': '原始发现', 'kind': 'competitor', 'platform': '抖音',
        'source_url': 'https://www.douyin.com/video/7390000000000000001', 'summary': '原始记录'})
    payload = save_payload(client) | {'opportunity_id': opportunity['id']}
    original = create_plan(db, ctx, 'p', payload['plan'] | {'opportunity_id': opportunity['id']})
    db.commit()
    response = client.post(ROOT + '/plan', json=payload)
    assert response.status_code == 409, response.text
    assert 'reference_analysis' not in db.get(ContentPlan, original['id']).source_snapshot
    assert len(db.scalars(select(ContentPlan)).all()) == 1


def test_generation_projection_excludes_raw_source_and_only_exports_safe_technique_context(breakdown_client):
    client, _, _, _ = breakdown_client
    payload = save_payload(client)
    saved = client.post(ROOT + '/plan', json=payload)
    assert saved.status_code == 200, saved.text
    from app.video_breakdown import generation_reference_context
    projection = generation_reference_context(saved.json()['source'])
    assert projection['source_url'] == 'https://www.douyin.com/video/7390000000000000001'
    assert projection['reference_analysis']['techniques'] == ['问题引入', '具体数字入口', '实拍动线']
    assert projection['reference_analysis']['template']['id'] == 'question'
    assert len(projection['reference_analysis']['source_digest']) == 64
    serialized = json.dumps(projection, ensure_ascii=False)
    assert all(text not in serialized for text in ['888万元', '400㎡', '参考豪宅视频', '从玄关推镜到客厅'])
    tampered = json.loads(json.dumps(saved.json()['source']))
    tampered['reference_analysis']['reuse_template'] = {'id': 'invented', 'label': '888万元', 'description': '400㎡'}
    assert generation_reference_context(tampered) == {}
    assert generation_reference_context({'summary': '普通来源'}) == {}


@pytest.mark.parametrize('bad_template', [[], {'id': []}, {'id': {'unsafe': True}}])
def test_generation_projection_fails_closed_for_invalid_stored_template(bad_template):
    from app.video_breakdown import generation_reference_context
    snapshot = {'reference_analysis': {'schema_version': 1, 'content_sha256': 'a'*64,
        'reference': {'source_url': 'https://www.douyin.com/video/7390000000000000001'},
        'structure': {'gaps': []}, 'reuse_template': bad_template}}
    assert generation_reference_context(snapshot) == {}


def test_generation_projection_keeps_malformed_gaps_out_of_generation():
    from app.video_breakdown import generation_reference_context
    snapshot = {'reference_analysis': {'schema_version': 1, 'content_sha256': 'a'*64,
        'reference': {'source_url': 'https://www.douyin.com/video/7390000000000000001'},
        'structure': {'gaps': None}, 'reuse_template': {'id': 'question'}}}
    assert generation_reference_context(snapshot)['reference_analysis']['timing']['gaps'] == []


def test_sqlite_simultaneous_reference_saves_create_only_one_plan(tmp_path):
    from app.discovery import router
    engine = create_engine(f'sqlite:///{tmp_path / "concurrent.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id='t', name='团队'))
        db.flush()
        db.add(Property(id='p', tenant_id='t', name='住宅', city='苏州'))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: Context('u', 't', 'content', '编辑', 'editor@example.org')
    payload = {'reference': {'source_url': SOURCE, 'title': '住宅参考', 'transcript': '[00:00-00:03] 怎么看房？',
        'topic_id': 'space'}, 'plan': {'title': '住宅拍摄计划', 'angle': '资料核对', 'brief': '当前项目草稿',
        'topic_id': 'space', 'channel': '抖音'}, 'idempotency_key': '8ed9aee8-0d6c-4f27-9835-75bd78444581'}
    barrier = Barrier(8)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=8) as pool:
        def save(_):
            barrier.wait(timeout=5)
            return client.post(ROOT + '/plan', json=payload)
        responses = list(pool.map(save, range(8)))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    assert len({response.json()['id'] for response in responses}) == 1
    with Session(engine) as db:
        assert len(db.scalars(select(ContentPlan)).all()) == 1
        assert db.get(Property, 'p').name == '住宅'
    engine.dispose()
