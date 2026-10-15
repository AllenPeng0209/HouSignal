"""Planning retains its real sources and applies registered methods to model work."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from threading import Event, Timer

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import jobs, operations, provider
from app.agent_models import ModelUsage
from app.core_models import Fact, Membership
from app.db import get_db
from app.job_models import Job
from app.operations_models import ContentOpportunity, ContentPlan
from app.security import get_context
from test_business import scenario
from test_discovery import discovery_client
from test_operations import operations_client


ROOT = '/api/projects/p/operations'
CONFIG = {'content_type': 'professional', 'subject': '户型点评', 'target_seconds': 45,
          'skill_ids': ['topic-matrix', 'titles-and-hooks'], 'hook': '面积相同，动线为何不同？',
          'outline': ['提出日常动线问题', '用项目事实核对'], 'cta': '说说你最在意的动线'}
REQUEST = {'content_type': 'professional', 'subject': '户型点评', 'topic_id': 'space',
           'account_id': None, 'channel': '抖音', 'reference_id': 'reference',
           'skill_ids': ['topic-matrix', 'titles-and-hooks'], 'intent': '帮助改善家庭判断空间尺度',
           'target_seconds': 45}


@pytest.fixture
def planning_client(discovery_client, monkeypatch):
    _, factory, current, ctx = discovery_client
    monkeypatch.setattr(provider.settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(provider.settings, 'dashscope_chat_model', 'qwen-plus')
    with factory() as db:
        db.add_all([
            ContentOpportunity(id='reference', tenant_id='t', property_id='p', title='400㎡也显挤？',
                platform='抖音', kind='trend', source_key='douyin:7390000000000000001',
                source_url='https://www.douyin.com/video/7390000000000000001',
                research_notes={'transcript': '[00:00-00:03] 400㎡也显挤？',
                                'observations': '[00:03-00:08] 对照户型图', 'target_seconds': 30, 'topic_id': 'space'},
                source_snapshot={'qualification': 'candidate_unverified'}, revision=2),
            ContentOpportunity(id='other-reference', tenant_id='t', property_id='p2', title='其他项目参考',
                platform='抖音', kind='trend', source_url='https://www.douyin.com/video/7390000000000000002'),
            Fact(id='fact', tenant_id='t', property_id='p', semantic_key='area', label='面积',
                 value='120', unit='㎡', source='测绘文件', status='verified', revision=1),
        ])
        db.commit()
    app = FastAPI()
    app.include_router(operations.router)
    app.include_router(jobs.router)
    if importlib.util.find_spec('app.content_planning'):
        from app import content_planning
        app.include_router(content_planning.router)
    def database():
        with factory() as db:
            try:
                yield db
            except Exception:
                db.rollback()
                raise
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: current['ctx']
    with TestClient(app) as client:
        yield client, factory, current, ctx


def suggestions():
    return {'candidates': [
        {'title': title, 'angle': angle, 'hook': hook, 'outline': ['提出问题', '核对项目面积', '说明适用条件'],
         'cta': '留言说说你的需求', 'topic_id': 'space', 'why': why,
         'missing': ['户型图与现场核对'], 'reference_note': '借鉴提问结构，原片事实不用于本项目'}
        for title, angle, hook, why in [
            ('空间尺度怎么判断', '从家庭日常需求开始', '面积数字如何对应生活？', '用问题组织资料'),
            ('别只看面积数字', '对比判断方式', '相同数字等于相同体验吗？', '用对比解释选择'),
            ('看房时带上这两个问题', '给出判断清单', '看房前该问什么？', '用清单帮助决策'),
        ]]}


def model_reply(data):
    return {'model': 'planning-fixture', 'choices': [{'finish_reason': 'stop',
        'message': {'role': 'assistant', 'content': json.dumps(data, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 80, 'completion_tokens': 60}}


def queued(client, **changes):
    response = client.post(ROOT + '/planning/suggest', json=REQUEST | changes)
    assert response.status_code == 200, response.text
    return response.json()['job_id']


def test_saved_plan_recovers_configuration_and_reusable_reference_evidence(planning_client):
    client, factory, _, _ = planning_client
    response = client.post(ROOT + '/plans', json={'title': '尺度问题', 'topic_id': 'space',
        'reference_id': 'reference', 'planning': CONFIG})
    assert response.status_code == 200, response.text
    saved = client.get(ROOT + '/plans').json()[0]
    assert saved['planning'] == CONFIG
    assert saved['reference_id'] == 'reference'
    assert saved['source']['planning_reference']['revision'] == 2
    assert saved['source']['planning_reference']['source_url'].endswith('7390000000000000001')
    second = client.post(ROOT + '/plans', json={'title': '另一个角度', 'topic_id': 'space',
        'reference_id': 'reference', 'planning': CONFIG})
    assert second.status_code == 200
    assert second.json()['id'] != saved['id']
    with factory() as db:
        assert db.get(ContentPlan, saved['id']).source_snapshot['planning'] == CONFIG


def test_reference_url_is_canonical_in_plan_and_model_context(planning_client, monkeypatch):
    from app.content_planning import handle_content_planning
    client, factory, _, _ = planning_client
    canonical = 'https://www.douyin.com/video/7390000000000000001'
    with factory() as db:
        reference = db.get(ContentOpportunity, 'reference')
        reference.source_url = canonical + '?access_token=synthetic-private-token'
        reference.source_snapshot = {'url': reference.source_url, 'qualification': 'candidate_unverified'}
        db.commit()
    saved = client.post(ROOT + '/plans', json={'title': '尺度问题', 'topic_id': 'space',
        'reference_id': 'reference', 'planning': CONFIG})
    assert saved.status_code == 200
    snapshot = saved.json()['source']['planning_reference']
    assert snapshot['source_url'] == canonical
    assert snapshot['source_evidence']['url'] == canonical
    seen = []
    def model(messages, **_kwargs):
        seen.append(json.loads(messages[1]['content']))
        return model_reply(suggestions())
    monkeypatch.setattr(provider, 'chat', model)
    jid = queued(client)
    with factory() as db:
        output = handle_content_planning(db, db.get(Job, jid))
    assert seen[0]['reference']['source_url'] == canonical
    assert 'synthetic-private-token' not in str(seen[0])
    assert output['planning_suggestions']['reference']['source_url'] == canonical


def test_planning_edit_detaches_old_script_but_schedule_edit_does_not(scenario):
    db, ctx, *_, content = scenario
    plan = operations.create_plan(db, ctx, 'p', {'title': '问题', 'topic_id': 'space', 'planning': CONFIG})
    record = db.get(ContentPlan, plan['id'])
    record.content_version_id = content.id
    db.commit()
    operations.update_plan(db, ctx, 'p', record.id, {'scheduled_for': '2026-10-20', 'status': 'scheduled'})
    assert record.content_version_id == content.id
    changed = operations.update_plan(db, ctx, 'p', record.id, {'planning': CONFIG | {'hook': '新的开场问题'}})
    assert changed['content_version_id'] is None
    assert changed['planning']['hook'] == '新的开场问题'


@pytest.mark.parametrize('change,code', [
    ({'reference_id': 'other-reference'}, 404),
    ({'planning': CONFIG | {'content_type': 'invented'}}, 422),
    ({'planning': CONFIG | {'target_seconds': 90}}, 422),
    ({'planning': CONFIG | {'skill_ids': ['platform-publishing']}}, 422),
    ({'planning': CONFIG | {'instructions': ['untrusted instruction']}}, 422),
    ({'source_snapshot': {'planning': CONFIG}}, 422),
])
def test_plan_rejects_foreign_reference_and_client_owned_methods(planning_client, change, code):
    client, _, _, _ = planning_client
    response = client.post(ROOT + '/plans', json={'title': '问题', 'topic_id': 'space',
        'planning': CONFIG, **change})
    assert response.status_code == code
    assert client.get(ROOT + '/plans').json() == []


def test_suggest_only_enqueues_owned_context_and_never_calls_model_in_request(planning_client, monkeypatch):
    client, factory, _, _ = planning_client
    monkeypatch.setattr(provider, 'chat', lambda *_args, **_kwargs: pytest.fail('API must enqueue'))
    jid = queued(client)
    with factory() as db:
        job = db.get(Job, jid)
        assert (job.kind, job.status, job.user_id, job.property_id) == ('content_planning', 'queued', 'u', 'p')
        assert job.payload == REQUEST


def test_suggest_applies_methods_and_returns_three_model_candidates_with_reload_context(planning_client, monkeypatch):
    client, factory, _, _ = planning_client
    jid = queued(client)
    from app.content_planning import handle_content_planning
    seen = []
    def model(messages, **kwargs):
        seen.append(json.loads(messages[1]['content']))
        return model_reply(suggestions())
    monkeypatch.setattr(provider, 'chat', model)
    with factory() as db:
        result = handle_content_planning(db, db.get(Job, jid))
        db.commit()
        output = result['planning_suggestions']
        assert len(output['candidates']) == 3
        assert len({row['id'] for row in output['candidates']}) == 3
        assert output['candidates'][1]['title'] == '别只看面积数字'
        assert output['context'] == REQUEST
        assert seen[0]['content_type']['purpose']
        assert {row['id'] for row in seen[0]['methods']} == {'topic-matrix', 'titles-and-hooks'}
        assert all(row['instructions'] and row['output_requirements'] for row in seen[0]['methods'])
        assert seen[0]['reference']['evidence_basis'] == 'user_supplied_notes'
        assert seen[0]['reference']['research_notes']['transcript'].startswith('[00:00-00:03]')
        assert seen[0]['verified_facts'][0]['value'] == '120'
        assert all(row['value'] != '400' for row in seen[0]['verified_facts'])
        assert '未核验' in output['source_note']
        usage = db.scalars(select(ModelUsage).where(ModelUsage.job_id == jid)).all()
        assert [(row.input_tokens, row.output_tokens) for row in usage] == [(80, 60)]


def test_adopted_candidate_keeps_server_receipt_and_rejects_other_project_job(planning_client):
    client, factory, _, _ = planning_client
    with factory() as db:
        db.add_all([Job(id='suggestion', kind='content_planning', status='succeeded', tenant_id='t',
            property_id='p', user_id='u', payload=REQUEST,
            result={'planning_suggestions': {'context': REQUEST, 'candidates': [suggestions()['candidates'][0] | {'id': 'candidate-1'}]}}),
            Job(id='foreign-suggestion', kind='content_planning', status='succeeded', tenant_id='t',
                property_id='p2', user_id='u', payload=REQUEST,
                result={'planning_suggestions': {'context': REQUEST, 'candidates': [suggestions()['candidates'][0] | {'id': 'candidate-1'}]}})])
        db.commit()
    payload = {'title': '可编辑的新标题', 'topic_id': 'space', 'reference_id': 'reference',
        'planning': CONFIG | {'suggestion_job_id': 'suggestion', 'candidate_id': 'candidate-1'}}
    response = client.post(ROOT + '/plans', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['source']['planning_suggestion']['candidate']['title'] == '空间尺度怎么判断'
    bad = client.post(ROOT + '/plans', json=payload | {'planning': payload['planning'] | {'suggestion_job_id': 'foreign-suggestion'}})
    assert bad.status_code == 404


def test_adoption_uses_candidate_topic_and_rejects_reference_changed_since_suggestion(planning_client, monkeypatch):
    from app.content_planning import handle_content_planning
    client, factory, _, _ = planning_client
    jid = queued(client)
    choices = suggestions()
    choices['candidates'][1]['topic_id'] = 'layout'
    monkeypatch.setattr(provider, 'chat', lambda *_args, **_kwargs: model_reply(choices))
    with factory() as db:
        job = db.get(Job, jid)
        job.result = handle_content_planning(db, job)
        job.status = 'succeeded'; db.commit()
        candidate = job.result['planning_suggestions']['candidates'][1]
    payload = {'title': candidate['title'], 'topic_id': 'layout', 'reference_id': 'reference',
        'planning': CONFIG | {'suggestion_job_id': jid, 'candidate_id': candidate['id']}}
    assert client.post(ROOT + '/plans', json=payload).status_code == 200
    with factory() as db:
        reference = db.get(ContentOpportunity, 'reference')
        reference.revision += 1; reference.note = '参考已变化'; db.commit()
    response = client.post(ROOT + '/plans', json=payload)
    assert response.status_code == 409
    assert '参考' in response.json()['detail']


def test_suggest_rejects_budget_unready_method_and_reader_before_queue(planning_client, monkeypatch):
    client, factory, current, ctx = planning_client
    current['ctx'] = replace(ctx, role='viewer')
    assert client.post(ROOT + '/planning/suggest', json=REQUEST).status_code == 403
    current['ctx'] = ctx
    monkeypatch.setattr(provider.settings, 'dashscope_api_key', '')
    assert client.post(ROOT + '/planning/suggest', json=REQUEST).status_code == 409
    monkeypatch.setattr(provider.settings, 'dashscope_api_key', 'synthetic-key')
    monkeypatch.setattr(provider.settings, 'development_token_budget', 1)
    with factory() as db:
        db.add(Job(id='prior-call', tenant_id='t', property_id='p', user_id='u', kind='chat', status='succeeded'))
        db.flush()
        db.add(ModelUsage(job_id='prior-call', tenant_id='t', property_id='p', model='cloud-fixture', input_tokens=2, output_tokens=0))
        db.commit()
    assert client.post(ROOT + '/planning/suggest', json=REQUEST).status_code == 429
    with factory() as db:
        assert db.scalars(select(Job).where(Job.kind == 'content_planning')).all() == []


@pytest.mark.parametrize('invalid', ['not json', {'candidates': []}, {'candidates': suggestions()['candidates'][:1]}])
def test_invalid_model_reply_fails_after_bounded_repair_without_static_fallback(planning_client, monkeypatch, invalid):
    client, factory, _, _ = planning_client
    jid = queued(client)
    from app.content_planning import handle_content_planning
    calls = []
    def model(messages, **_kwargs):
        calls.append(messages)
        reply = model_reply(invalid)
        if isinstance(invalid, str):
            reply['choices'][0]['message']['content'] = invalid
        return reply
    monkeypatch.setattr(provider, 'chat', model)
    with factory() as db:
        with pytest.raises(provider.ProviderError, match='策划'):
            handle_content_planning(db, db.get(Job, jid))
        assert len(calls) == 2
        assert db.get(Job, jid).result == {}
        assert len(db.scalars(select(ModelUsage).where(ModelUsage.job_id == jid)).all()) == 2


def test_worker_executes_planning_lane_and_keeps_usage_after_rejected_reply(planning_client, monkeypatch):
    from app.worker import claim_job, execute_job
    client, factory, _, _ = planning_client
    jid = queued(client)
    monkeypatch.setattr(provider, 'chat', lambda *_args, **_kwargs: model_reply({'candidates': []}))
    with factory() as db:
        claimed = claim_job(db, kinds=('content_planning',))
        ticket = (claimed.id, claimed.lease_token, claimed.attempt)
    assert execute_job(*ticket, session_factory=factory) is False
    with factory() as db:
        assert db.get(Job, jid).status == 'failed'
        assert len(db.scalars(select(ModelUsage).where(ModelUsage.job_id == jid)).all()) == 2


def test_script_generation_receives_category_methods_and_structured_duration(scenario, monkeypatch):
    from app.business import handle_generate
    db, ctx, *_ = scenario
    monkeypatch.setattr(provider.settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(provider.settings, 'dashscope_api_key', 'synthetic-key')
    monkeypatch.setattr(provider.settings, 'dashscope_chat_model', 'qwen-plus')
    plan = operations.create_plan(db, ctx, 'p', {'title': '尺度问题', 'topic_id': 'space', 'planning': CONFIG})
    db.commit()
    seen = []
    def model(data, **_callbacks):
        seen.append(data)
        return {'topic': '尺度问题', 'script': '420㎡的空间资料。', 'cover_title': '空间资料',
            'fact_refs': [{'id': 'f', 'revision': 1}],
            'storyboard': [{'asset_id': 'a', 'duration': 45, 'start': 0, 'caption': '420㎡空间'}]}
    monkeypatch.setattr(provider, 'generate_content', model)
    result = handle_generate(db, Job(id='planning-generation', user_id='admin', tenant_id='tenant',
        property_id='p', kind='generate_content', payload={'topic_id': 'space', 'plan_id': plan['id']}))
    assert result['plan_id'] == plan['id']
    assert seen[0]['target_duration_seconds'] == 45
    context = seen[0]['planning_context']
    assert context['planning']['hook'] == CONFIG['hook']
    assert context['content_type']['id'] == 'professional'
    assert all(row['instructions'] for row in context['methods'])


@pytest.mark.parametrize('structured', [True, False])
def test_structured_planning_shares_verified_fact_scope_with_suggestions(scenario, monkeypatch, structured):
    from app.business import handle_generate
    from app.business_models import ContentVersion
    from app.core_models import Property
    db, ctx, *_ = scenario
    monkeypatch.setattr(provider.settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(provider.settings, 'dashscope_api_key', 'synthetic-key')
    monkeypatch.setattr(provider.settings, 'dashscope_chat_model', 'qwen-plus')
    db.add(Property(id='unrelated', tenant_id='tenant', name='其他项目', city='苏州'))
    db.flush()
    db.add_all([
        Fact(id='layout', tenant_id='tenant', property_id='p', semantic_key='layout', label='户型',
             value='三房两厅', unit='', source='户型图', status='verified', revision=2),
        Fact(id='width', tenant_id='tenant', property_id='p', semantic_key='living_width', label='开间',
             value='6', unit='米', source='测绘文件', status='verified', revision=3),
        Fact(id='draft-price', tenant_id='tenant', property_id='p', semantic_key='price', label='报价',
             value='888', unit='万元', source='待核对', status='pending', revision=1),
        Fact(id='expired-height', tenant_id='tenant', property_id='p', semantic_key='height', label='层高',
             value='3.2', unit='米', source='旧资料', status='verified', revision=1,
             valid_until=datetime.now(timezone.utc) - timedelta(days=1)),
        Fact(id='foreign-tenant', tenant_id='other', property_id='p', semantic_key='other-area', label='其他团队面积',
             value='999', unit='㎡', source='其他团队资料', status='verified', revision=1),
        Fact(id='foreign-project', tenant_id='tenant', property_id='unrelated', semantic_key='area', label='其他项目面积',
             value='777', unit='㎡', source='其他项目资料', status='verified', revision=1),
    ])
    db.commit()
    body = {'title': '户型与空间尺度', 'topic_id': 'layout'}
    if structured:
        body['planning'] = CONFIG | {'outline': ['结合420㎡与6米开间解释户型', '说明日常动线判断方法']}
    plan = operations.create_plan(db, ctx, 'p', body)
    db.commit()
    seen = []
    def model(data, **_callbacks):
        seen.append(data)
        # The external model can cite only IDs supplied by the business handler.
        return {'topic': '户型资料', 'script': '结合420㎡与6米开间核对户型资料。' if structured else '核对户型资料。',
            'cover_title': '户型资料', 'fact_refs': [{'id': row['id'], 'revision': row['revision']} for row in data['facts']],
            'storyboard': [{'asset_id': 'a', 'duration': 45 if structured else 30, 'start': 0, 'caption': '户型资料'}]}
    monkeypatch.setattr(provider, 'generate_content', model)
    result = handle_generate(db, Job(id='structured-fact-scope', user_id='admin', tenant_id='tenant',
        property_id='p', kind='generate_content', payload={'topic_id': 'layout', 'plan_id': plan['id']}))
    assert {row['id'] for row in seen[0]['facts']} == ({'f', 'layout', 'width'} if structured else {'layout'})
    assert seen[0]['topic']['id'] == 'layout'
    assert seen[0]['topic']['eligible'] is True
    content = db.get(ContentVersion, result['content_version_id'])
    assert {row['id'] for row in content.fact_refs} == ({'f', 'layout', 'width'} if structured else {'layout'})


def test_reference_source_copy_is_not_sent_to_script_model(scenario, monkeypatch):
    from app.business import handle_generate
    db, ctx, *_ = scenario
    monkeypatch.setattr(provider.settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(provider.settings, 'dashscope_api_key', 'synthetic-key')
    monkeypatch.setattr(provider.settings, 'dashscope_chat_model', 'qwen-plus')
    db.add(ContentOpportunity(id='reference', tenant_id='tenant', property_id='p', title='888万元的400㎡豪宅',
        kind='trend', platform='抖音', source_url='https://www.douyin.com/video/7390000000000000001',
        research_notes={'transcript': '[00:00-00:03] 888万元买400㎡？', 'observations': ''}))
    db.commit()
    plan = operations.create_plan(db, ctx, 'p', {'title': '尺度问题', 'topic_id': 'space',
        'reference_id': 'reference', 'planning': CONFIG})
    db.commit()
    seen = []
    def model(data, **_callbacks):
        seen.append(data)
        return {'topic': '尺度问题', 'script': '420㎡的空间资料。', 'cover_title': '空间资料',
            'fact_refs': [{'id': 'f', 'revision': 1}],
            'storyboard': [{'asset_id': 'a', 'duration': 45, 'start': 0, 'caption': '420㎡空间'}]}
    monkeypatch.setattr(provider, 'generate_content', model)
    handle_generate(db, Job(id='safe-planning-generation', user_id='admin', tenant_id='tenant',
        property_id='p', kind='generate_content', payload={'topic_id': 'space', 'plan_id': plan['id']}))
    assert '400㎡' not in str(seen[0])
    assert '888万元' not in str(seen[0])
    assert seen[0]['planning_context']['reference']['reference_id'] == 'reference'
    assert {row['id'] for row in seen[0]['planning_context']['reference']['techniques']} == {'question', 'numeric_anchor'}


def test_cancelled_planning_and_revoked_membership_do_not_call_model(planning_client, monkeypatch):
    from app.content_planning import handle_content_planning
    from app.worker import LostLease
    client, factory, _, _ = planning_client
    jid = queued(client)
    monkeypatch.setattr(provider, 'chat', lambda *_args, **_kwargs: pytest.fail('must not call model'))
    with factory() as db:
        db.info['job_cancelled'] = lambda: True
        with pytest.raises(LostLease):
            handle_content_planning(db, db.get(Job, jid))
        db.info.pop('job_cancelled')
        member = db.scalar(select(Membership).where(Membership.user_id == 'u', Membership.tenant_id == 't'))
        member.role = 'viewer'; db.commit()
        with pytest.raises(HTTPException) as exc:
            handle_content_planning(db, db.get(Job, jid))
        assert exc.value.status_code == 403


def test_reference_changed_during_suggestion_rejects_old_model_reply(planning_client, monkeypatch):
    from app.content_planning import handle_content_planning
    client, factory, _, _ = planning_client
    jid = queued(client)
    def model(*_args, **_kwargs):
        with factory() as other:
            reference = other.get(ContentOpportunity, 'reference')
            reference.note = '新内容'; reference.revision += 1; other.commit()
        return model_reply(suggestions())
    monkeypatch.setattr(provider, 'chat', model)
    with factory() as db:
        with pytest.raises(HTTPException) as exc:
            handle_content_planning(db, db.get(Job, jid))
        assert exc.value.status_code == 409
        assert db.get(Job, jid).result == {}


def test_suggestion_cannot_transplant_reference_quantity_into_target_hook(planning_client, monkeypatch):
    from app.content_planning import handle_content_planning
    client, factory, _, _ = planning_client
    jid = queued(client)
    bad = suggestions()
    bad['candidates'][0]['hook'] = '本项目400㎡如何容纳一家人的日常？'
    monkeypatch.setattr(provider, 'chat', lambda *_args, **_kwargs: model_reply(bad))
    with factory() as db:
        with pytest.raises(provider.ProviderError, match='策划'):
            handle_content_planning(db, db.get(Job, jid))
        assert db.get(Job, jid).result == {}


def test_worker_picks_up_planning_in_real_background_lane(planning_client, monkeypatch):
    from app.worker import run_worker
    client, factory, _, _ = planning_client
    jid = queued(client)
    stop = Event()
    def model(*_args, **_kwargs):
        stop.set()
        return model_reply(suggestions())
    monkeypatch.setattr(provider, 'chat', model)
    timeout = Timer(4, stop.set)
    timeout.start()
    try:
        run_worker(stop, session_factory=factory)
    finally:
        timeout.cancel()
    with factory() as db:
        job = db.get(Job, jid)
        assert job.status == 'succeeded'
        assert len(job.result['planning_suggestions']['candidates']) == 3
