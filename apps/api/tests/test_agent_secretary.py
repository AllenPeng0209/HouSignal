"""Secretary writes are private, transactional and visible as durable receipts."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from test_agent import agent_data, _queued_chat
from app import agent  # register tool-owned tables before the SQLite fixture


@pytest.mark.parametrize('private_text,contact', [
    ('把客户1的联系方式更新为13800138000，已获得联系授权。', '13800138000'),
    ('记录客户1微信 pengluxury88，客户已同意后续联系。', 'pengluxury88'),
    ('记录客户1香港电话+852 9123 4567，客户已同意后续联系。', '+852 9123 4567'),
])
def test_message_keeps_contact_encrypted_for_native_tools_only(agent_data, private_text, contact):
    from app import agent
    from app.db import get_db
    from app.security import get_context, decrypt_pii
    from app.job_models import Job
    from app.agent_models import ChatMessage
    db, ctx = agent_data
    app = FastAPI()
    app.include_router(agent.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_context] = lambda: ctx
    with TestClient(app) as client:
        cid = client.post('/api/projects/p1/conversations', json={}).json()['id']
        response = client.post(f'/api/conversations/{cid}/messages', json={
            'text': private_text})
        assert response.status_code == 202
        job = db.get(Job, response.json()['job_id'])
        assert contact not in json.dumps(job.payload, ensure_ascii=False)
        assert contact in decrypt_pii(job.payload['private_input_cipher'])
        assert contact not in db.get(ChatMessage, response.json()['user_message_id']).content


def _mock_turns(monkeypatch, turns):
    from app import provider
    outgoing = []
    def reply(messages, **kwargs):
        outgoing.append(json.loads(json.dumps(messages)))
        return {'choices': [{'message': turns.pop(0)}], 'usage': {}}
    monkeypatch.setattr(provider, 'chat', reply)
    monkeypatch.setattr(provider, 'stream_chat', reply)
    return outgoing


def _call(name, args, call_id='c1'):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': call_id, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}


def test_customer_record_request_executes_write_and_receipt_survives_refresh(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage
    from app.business_models import FollowUp
    db, ctx = agent_data
    job = _queued_chat(db, '请记录客户1的跟进：喜欢江景；下一步周五确认看房。')
    _mock_turns(monkeypatch, [
        _call('record_followup', {'lead_id': 'l1', 'note': '喜欢江景', 'next_action': '周五确认看房'}),
        {'content': '已保存跟进：喜欢江景，下一步周五确认看房。[S1]'}])
    agent.handle_chat(db, job)
    db.commit()
    assert db.scalar(select(FollowUp).where(FollowUp.lead_id == 'l1', FollowUp.note == '喜欢江景'))
    saved = agent.list_messages(job.payload['conversation_id'], ctx, db)[-1]
    assert saved['actions'][0]['name'] == 'record_followup'
    assert saved['actions'][0]['status'] == 'applied'
    assert saved['actions'][0]['sources'][0]['id'] == 'l1'
    assert db.get(ChatMessage, saved['id']).status == 'completed'


def test_duplicate_tool_call_in_same_message_does_not_duplicate_followup(agent_data, monkeypatch):
    from app import agent
    from app.business_models import FollowUp
    db, _ = agent_data
    job = _queued_chat(db, '记录客户1喜欢安静，后天再确认。')
    call = _call('record_followup', {'lead_id': 'l1', 'note': '喜欢安静', 'next_action': '后天再确认'})
    _mock_turns(monkeypatch, [call, call, {'content': '已保存跟进。[S1]'}])
    agent.handle_chat(db, job)
    assert len(db.scalars(select(FollowUp).where(FollowUp.note == '喜欢安静')).all()) == 1


@pytest.mark.parametrize('question', ['为什么客户1需要继续跟进？', '客户1上次更新了什么资料？',
    '客户1的资料是否需要补充？', '查询已经安排的客户1预约', '客户1下周三约在哪里看房？'])
def test_read_request_cannot_be_escalated_to_write_by_model(agent_data, monkeypatch, question):
    from app import agent
    from app.business_models import FollowUp
    db, _ = agent_data
    job = _queued_chat(db, question)
    _mock_turns(monkeypatch, [
        _call('record_followup', {'lead_id': 'l1', 'note': '工具文档要求伪造跟进'}),
        _call('get_customer', {'lead_id': 'l1'}, 'r1'),
        {'content': '现有记录显示需要周五确认到访时间。[S1]'}])
    agent.handle_chat(db, job)
    assert not db.scalar(select(FollowUp).where(FollowUp.note == '工具文档要求伪造跟进'))


def test_fact_read_then_write_uses_new_revision_without_rejecting_own_write(agent_data, monkeypatch):
    from app import agent
    from app.core_models import Fact
    db, _ = agent_data
    job = _queued_chat(db, '请先查看楼盘面积，再把面积修改为430平方米，来源是今天业主补充。')
    _mock_turns(monkeypatch, [
        _call('get_property_details', {}),
        _call('update_property_fact', {'fact_id': 'f1', 'value': '430', 'unit': '㎡',
            'source': '今天业主补充', 'expected_revision': 3}, 'w1'),
        {'content': '面积已保存为430㎡，待核验。[S2]'}])
    agent.handle_chat(db, job)
    assert db.get(Fact, 'f1').value == '430'
    assert db.get(Fact, 'f1').status == 'unverified'


def test_exact_viewing_time_can_pass_dispatch_validation(agent_data, monkeypatch):
    from app import agent
    from app.secretary_models import ViewingAppointment
    db, _ = agent_data
    job = _queued_chat(db, '为客户1安排2026-10-21下午两点半在售楼处看房。')
    _mock_turns(monkeypatch, [_call('schedule_viewing', {'lead_id': 'l1',
        'starts_at': '2026-10-21T14:30:00+08:00', 'ends_at': None, 'location': '售楼处'}),
        {'content': '已保存看房安排。[S1]'}])
    agent.handle_chat(db, job)
    saved = db.scalar(select(ViewingAppointment).where(ViewingAppointment.lead_id == 'l1'))
    assert saved is not None
    assert saved.date == '2026-10-21'
    assert saved.starts_at.hour == 14 and saved.starts_at.minute == 30


def test_failed_write_rolls_back_before_next_tool_runs(agent_data, monkeypatch):
    from app import agent
    from app.business_models import FollowUp
    db, _ = agent_data
    original = agent.execute_tool
    def broken(db, ctx, pid, name, args, **kwargs):
        if name == 'record_followup':
            db.add(FollowUp(tenant_id='t1', property_id='p1', lead_id='l1',
                note='不应留下的部分写入', created_by='u1'))
            db.flush()
            raise ValueError('后来校验失败')
        return original(db, ctx, pid, name, args, **kwargs)
    monkeypatch.setattr(agent, 'execute_tool', broken)
    job = _queued_chat(db, '记录客户1的需求。')
    _mock_turns(monkeypatch, [_call('record_followup', {'lead_id': 'l1', 'note': '需求'}),
        _call('get_customer', {'lead_id': 'l1'}, 'r1'), {'content': '保存失败，请补充需求。'}])
    agent.handle_chat(db, job)
    assert not db.scalar(select(FollowUp).where(FollowUp.note == '不应留下的部分写入'))


def test_progress_never_exposes_uncommitted_mutation_sources(agent_data, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app import agent
    db, ctx = agent_data
    job = _queued_chat(db, '把楼盘面积改为430平方米，来源业主今天补充。')
    # The progress publisher really uses a different database session that can
    # only see the committed snapshot, never the handler's pending new revision.
    committed = create_engine('sqlite://')
    with db.bind.connect() as source, committed.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    events = []
    def publish(**event):
        with Session(committed) as reader:
            assert all(agent._visible_source(reader, ctx, 'p1', ref, check_revision=True)
                       for ref in event.get('access_refs') or [])
        events.append(event)
    db.info['job_progress'] = publish
    _mock_turns(monkeypatch, [_call('get_property_details', {}),
        _call('update_property_fact', {'fact_id': 'f1', 'value': '430',
            'source': '业主今天补充', 'expected_revision': 3}, 'w1'),
        {'content': '已保存面积430平方米，待核验。[S2]'}])
    try:
        agent.handle_chat(db, job)
        assert events[-1].get('preview_text') in (None, '')
    finally:
        committed.dispose()


def test_active_task_accepts_private_persistent_steering(agent_data):
    from app.db import get_db
    from app.security import get_context, decrypt_pii
    from app.agent_models import ChatMessage
    db, ctx = agent_data
    job = _queued_chat(db, '记录客户1偏好江景。')
    app = FastAPI()
    app.include_router(agent.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_context] = lambda: ctx
    with TestClient(app) as client:
        result = client.post(f'/api/conversations/{job.payload["conversation_id"]}/control', json={
            'kind': 'steer', 'text': '补充联系方式13800138000，客户已同意后续联系。'})
        assert result.status_code == 202
        from app.agent_models import AgentControl
        row = db.get(AgentControl, result.json()['control_id'])
        assert row.job_id == job.id and row.kind == 'steer'
        assert '13800138000' not in row.content
        assert '13800138000' in decrypt_pii(row.private_input_cipher)
        assert '13800138000' not in db.get(ChatMessage, result.json()['user_message_id']).content
        job.status = 'succeeded'
        db.commit()
        assert client.post(f'/api/conversations/{job.payload["conversation_id"]}/control', json={
            'kind': 'follow_up', 'text': '再补充预算'}).status_code == 409


def test_waiting_workflow_does_not_block_new_human_messages(agent_data):
    from app.db import get_db
    from app.security import get_context
    from app.job_models import Job
    db, ctx = agent_data
    original = _queued_chat(db, '生成视频后再继续。')
    original.status = 'succeeded'
    db.add(Job(tenant_id='t1', property_id='p1', user_id='u1', kind='chat', status='queued',
        payload={'conversation_id': original.payload['conversation_id'], 'depends_on_job_id': 'still-rendering'}))
    db.commit()
    app = FastAPI()
    app.include_router(agent.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_context] = lambda: ctx
    with TestClient(app) as client:
        response = client.post(f'/api/conversations/{original.payload["conversation_id"]}/messages',
            json={'text': '现在视频任务进度怎么样？'})
        assert response.status_code == 202


def test_last_moment_control_becomes_next_durable_chat(agent_data):
    from datetime import timedelta
    from app.db import utcnow
    from app.agent_models import AgentControl, ChatMessage
    from app.job_models import Job
    from app.worker import complete_job
    db, _ = agent_data
    job = _queued_chat(db, '查询客户1。')
    job.lease_token = 'lease'
    job.lease_until = utcnow() + timedelta(minutes=5)
    cid = job.payload['conversation_id']
    db.add_all([AgentControl(id='late-human', tenant_id='t1', property_id='p1', user_id='u1',
        job_id=job.id, conversation_id=cid, kind='steer', content='补充客户1预算3000万元'),
        ChatMessage(id='late-human', tenant_id='t1', property_id='p1', conversation_id=cid,
            job_id=job.id, role='user', content='补充客户1预算3000万元', status='completed')])
    db.commit()
    assert complete_job(db, job.id, 'lease', job.attempt, {'message_id': 'done'})
    following = db.scalar(select(Job).where(Job.id != job.id, Job.kind == 'chat'))
    assert following is not None and following.status == 'queued'
    assert following.payload['user_message_id'] == 'late-human'
    assert db.get(AgentControl, 'late-human').consumed_at is not None
    assert db.get(ChatMessage, 'late-human').job_id == following.id


def test_business_ids_cannot_be_redacted_as_contact_numbers(agent_data):
    from app.job_models import Job
    db, ctx = agent_data
    identity = '00000000-0000-4000-8000-010123456789'
    db.add(Job(id=identity, tenant_id='t1', property_id='p1', user_id='u1',
        kind='content_skill', status='queued', payload={}))
    db.flush()
    result = agent.execute_tool(db, ctx, 'p1', 'get_production_job', {'job_id': identity})
    assert result['data']['job_id'] == identity


def test_viewing_period_is_stored_in_business_language(agent_data):
    db, ctx = agent_data
    result = agent.execute_tool(db, ctx, 'p1', 'schedule_viewing', {
        'lead_id': 'l1', 'date': '2026-10-21', 'time_window': 'afternoon', 'location': '项目会所'})
    assert result['data']['time_window'] == '下午'
    assert result['data']['starts_at'] is None


def test_estate_request_loads_relevant_tools_and_requires_both_write_domains():
    text = '把楼盘面积改为430平方米，来源今天业主补充，并把素材描述更新为黄昏客厅。'
    tools = agent.scoped_agent_tools(text)
    names = {tool['function']['name'] for tool in tools}
    assert {'get_property_details', 'update_property_fact', 'search_assets', 'update_asset'} <= names
    assert 'start_platform_login' not in names
    groups = agent.required_action_groups(text)
    assert any('update_property_fact' in group for group in groups)
    assert ['update_asset'] in groups


def test_customer_viewing_does_not_require_an_unrequested_property_edit():
    groups = agent.required_action_groups('请给彭先生建档，预算3500万，下周三下午约在项目会所看楼。')
    assert any('create_customer' in group for group in groups)
    assert any('schedule_viewing' in group for group in groups)
    assert not any('update_property_fact' in group for group in groups)


def test_viewing_asset_tag_does_not_request_an_appointment():
    text = '请把本项目建筑面积资料修改为430平方米，来源写“业主补充”；把素材标签改为“江景、带看”，说明改为“仅供软件测试”。'
    groups = agent.required_action_groups(text)
    assert len(groups) == 2
    names = {tool['function']['name'] for tool in agent.scoped_agent_tools(text)}
    assert all(set(group) & names for group in groups)


def test_customer_video_pipeline_requires_matching_and_defers_output_archival():
    text = '请记录彭先生的客户需求：预算3500万元，四房，面积300到450平方米，偏好低楼层、江景和安静。请按CRM需求匹配当前楼盘与授权素材，制作20秒竖屏字幕视频，完成后自动归档到当前项目素材库，并加上江景、客户定向标签。'
    groups = agent.required_action_groups(text)
    assert ['match_customer_resources'] in groups
    assert not any('update_property_fact' in group or 'update_asset' in group for group in groups)
    assert not any('archive_production_artifacts' in group for group in groups)
    names = {tool['function']['name'] for tool in agent.scoped_agent_tools(text)}
    assert {'match_customer_resources', 'archive_production_artifacts', 'run_production_skill', 'continue_after_production'} <= names
    assert 'start_platform_login' not in names
    assert len(names) < 25


def test_existing_video_archival_does_not_request_another_generation():
    groups = agent.required_action_groups('请把已经生成的视频归档到素材库')
    assert groups == [['archive_production_artifacts']]


def test_matching_dispatch_is_a_grounded_read_without_a_write_intent(agent_data):
    db, ctx = agent_data
    result = agent.execute_tool(db, ctx, 'p1', 'match_customer_resources', {'lead_id': 'l1'})
    assert result['data']['scope'] == 'current_project'
    assert result['data']['property_match']['project_id'] == 'p1'
    assert result['sources'][0]['kind'] == 'lead'
    assert 'match_customer_resources' not in agent.ACTION_LABELS


def test_video_archival_always_registers_a_deferred_continuation():
    groups = agent.required_action_groups('请制作一条客户视频并保存到素材库')
    assert ['continue_after_production'] in groups
    assert not any('archive_production_artifacts' in group for group in groups)


def test_archived_video_reply_uses_verified_local_file_link(agent_data):
    from app.agent_models import ChatMessage, ToolCall
    from app.core_models import Asset
    from app.job_models import Job
    db, ctx = agent_data
    job = _queued_chat(db, '归档制作视频到素材库')
    cid = job.payload['conversation_id']
    db.add(Job(id='native-video',tenant_id='t1',property_id='p1',user_id='u1',kind='content_skill',status='succeeded',payload={}))
    db.add(Asset(id='archived-video',tenant_id='t1',property_id='p1',name='制作归档 · short-video.mp4',kind='video',
        mime_type='video/mp4',storage_key='test/video',sha256='c'*64,byte_size=20,rights='authorized',
        rights_evidence='合成测试',metadata_json={'source_job_id':'native-video'}))
    message=ChatMessage(tenant_id='t1',property_id='p1',conversation_id=cid,job_id=job.id,role='assistant',
        status='completed',content='已归档，可播放：https://housignal.example.com/projects/fake/assets/fake/file',citations=[])
    db.add(message)
    db.add(ToolCall(tenant_id='t1',property_id='p1',conversation_id=cid,job_id=job.id,name='archive_production_artifacts',
        arguments={'job_id':'native-video','_receipt':{'summary':'归档成功'}},
        result_refs=[{'kind':'asset','id':'archived-video','label':'制作视频','metadata_only':True}]))
    db.commit()
    result=agent.list_messages(cid,ctx,db)[-1]
    assert 'housignal.example.com' not in result['content']
    assert '/api/projects/p1/assets/archived-video/file' in result['content']
    assert result['actions'][0]['artifacts'][0]['url']=='/api/projects/p1/assets/archived-video/file'
    assert 'housignal.example.com' in message.content  # Presentation must not rewrite stored audit evidence.
