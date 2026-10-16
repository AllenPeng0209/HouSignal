"""External tools share domain records without granting human review powers."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import importlib.util
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def test_business_tools_are_available_to_both_protocol_adapters():
    assert importlib.util.find_spec('app.external_agent_tools') is not None


@pytest.fixture
def business_tools(monkeypatch):
    from app import external_agent_tools as tools
    from app.db import Base, utcnow
    from app.core_models import Tenant, User, Property, Fact, Asset, Membership
    from app.operations_models import ContentOpportunity
    from app.security import Context, require_project
    from app.external_agent_auth import AgentIdentity, public_resource
    from app.external_agent_models import AgentOAuthClient, AgentOAuthGrant
    from app.config import settings
    import app.agent_models
    import app.audit_models
    monkeypatch.setitem(settings.__dict__, 'external_agent_origin', 'https://louxiang.example.test')
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='外部团队'),
                User(id='u', email='private@example.org', password_hash='hidden', name='编辑')])
    db.flush()
    db.add(Membership(id='member', user_id='u', tenant_id='t', role='content', active=True))
    db.add(AgentOAuthClient(id='client', name='ChatGPT', redirect_uris=['https://chatgpt.com/connector_platform_oauth_redirect']))
    db.flush()
    db.add(AgentOAuthGrant(id='grant', organization_id='t', user_id='u', client_id='client',
                           resource=public_resource(), scope='louxiang:read louxiang:write louxiang:production',
                           project_ids=['p'], expires_at=utcnow()+timedelta(days=1)))
    db.add_all([Property(id='p', tenant_id='t', name='陆家嘴公开案例', city='上海', channel='B站'),
                Property(id='p2', tenant_id='t', name='隔离项目', city='北京'),
                Property(id='foreign', tenant_id='other', name='其他团队', city='上海')])
    db.flush()
    db.add(Fact(id='fact', tenant_id='t', property_id='p', semantic_key='area', label='建面',
                value='504', unit='㎡', source='原作者公开介绍', status='unverified', required=True))
    db.add(Asset(id='asset', tenant_id='t', property_id='p', name='研究卡片.png', kind='image',
                 storage_key='private/internal.png', mime_type='image/png', sha256='a'*64,
                 byte_size=100, rights='authorized', rights_evidence='原创', origin_type='template',
                 metadata_json={'model_processing_authorized': False, 'usage_scope': 'editorial_original'}))
    db.add(ContentOpportunity(id='ref', tenant_id='t', property_id='p', title='陆家嘴豪宅研究',
                             kind='competitor', platform='B站', source_url='https://www.bilibili.com/video/BV1i7411e7hP/',
                             source_key='bilibili:BV1i7411e7hP', source_snapshot={'metrics': {'views': 9101829}},
                             note='原作者介绍504㎡，未作独立测量', research_notes={'observations': ['公开案例研究']}))
    db.commit()
    ctx = Context('u', 't', 'content', '编辑', 'private@example.org', None)
    identity = AgentIdentity(ctx=ctx, grant_id='grant', client_id='client',
                             scopes=frozenset({'louxiang:read', 'louxiang:write', 'louxiang:production'}),
                             project_ids=('p',))
    yield tools, db, identity
    db.close()
    engine.dispose()


def call(env, name, **arguments):
    tools, db, identity = env
    return tools.execute_agent_tool(db, identity, name, arguments)


def plan_body(**extra):
    return {'title': '三代同住，先看动线再看面积', 'angle': '研究家庭共享与独处的空间安排',
            'channel': 'B站', 'topic_id': 'layout', 'reference_id': 'ref',
            'brief': '公开案例研究；缺失老人房至卫生间动线、门槛和夜间照明镜头。',
            'planning': {'content_type': 'professional', 'subject': '三代同住家庭', 'target_seconds': 45,
                         'skill_ids': ['topic-evaluation', 'titles-and-hooks'],
                         'hook': '一家三代生活在一起，需要怎样的动线？',
                         'outline': ['共享空间', '独处空间', '待补镜头'], 'cta': '收藏这份观察清单'}, **extra}


def test_saved_draft_is_the_same_plan_seen_by_web_and_retains_reference_planning(business_tools):
    from app.operations import list_plans
    tools, db, actor = business_tools
    result = call(business_tools, 'save_plan', project_id='p', idempotency_key='plan-1', plan=plan_body())
    db.commit()
    saved = list_plans(db, actor.ctx, 'p')
    assert len(saved) == 1
    assert saved[0]['id'] == result['plan']['id']
    assert saved[0]['status'] == 'draft'
    assert saved[0]['reference_id'] == 'ref'
    assert saved[0]['planning']['target_seconds'] == 45
    assert saved[0]['source']['planning_reference']['source_url'] == 'https://www.bilibili.com/video/BV1i7411e7hP'
    assert result['receipt']['changed'] is True


def test_retry_returns_original_plan_without_duplicate_and_payload_conflict_is_rejected(business_tools):
    from app.operations_models import ContentPlan
    first = call(business_tools, 'save_plan', project_id='p', idempotency_key='same', plan=plan_body())
    business_tools[1].commit()
    second = call(business_tools, 'save_plan', project_id='p', idempotency_key='same', plan=plan_body())
    assert second['plan']['id'] == first['plan']['id']
    assert second['receipt']['changed'] is False
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'save_plan', project_id='p', idempotency_key='same', plan=plan_body(title='另一个选题'))
    assert error.value.status_code == 409
    assert business_tools[1].scalar(select(func.count()).select_from(ContentPlan)) == 1


def test_update_preserves_omitted_planning_and_reference_fields(business_tools):
    first = call(business_tools, 'save_plan', project_id='p', idempotency_key='a', plan=plan_body())
    changed = call(business_tools, 'update_plan', project_id='p', plan_id=first['plan']['id'],
                   idempotency_key='b', plan={'angle': '先比较动静分区'})
    assert changed['plan']['angle'] == '先比较动静分区'
    assert changed['plan']['planning']['outline'] == ['共享空间', '独处空间', '待补镜头']
    assert changed['plan']['reference_id'] == 'ref'


@pytest.mark.parametrize('field,value', [('marketing_confirmed', True), ('marketing_evidence', '已授权'),
    ('is_test', False), ('sales_owner_name', '销售'), ('tenant_id', 'other'), ('verified', True)])
def test_brief_never_accepts_privilege_or_review_fields(business_tools, field, value):
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'update_project_brief', project_id='p', idempotency_key='bad', brief={field: value})
    assert error.value.status_code == 422


def test_brief_edit_is_visible_in_existing_project_api_without_touching_authorization(business_tools):
    from app.core import project_dict
    from app.core_models import Property
    result = call(business_tools, 'update_project_brief', project_id='p', idempotency_key='brief',
                  brief={'audience': '三代同住改善家庭', 'owner_name': '内容研究组', 'channel': 'B站'})
    db, actor = business_tools[1:]
    saved = project_dict(db, actor.ctx, db.get(Property, 'p'))
    assert saved['audience'] == '三代同住改善家庭'
    assert saved['owner_name'] == '内容研究组'
    assert saved['marketing_confirmed'] is False
    assert result['project']['id'] == 'p'


def test_context_exposes_real_fact_status_and_missing_shots_without_private_storage(business_tools):
    result = call(business_tools, 'get_project_context', project_id='p')
    assert result['facts'][0]['status'] == 'unverified'
    assert result['facts'][0]['usable_for_marketing'] is False
    assert result['facts'][0]['source'] == '原作者公开介绍'
    assert result['gaps']['unverified_fact_ids'] == ['fact']
    assert result['assets'][0]['model_processing_authorized'] is False
    encoded = json.dumps(result, ensure_ascii=False)
    assert 'private/internal.png' not in encoded
    assert 'private@example.org' not in encoded


@pytest.mark.parametrize('name,args', [
    ('save_plan', {'idempotency_key': 'x', 'plan': plan_body()}),
    ('update_project_brief', {'idempotency_key': 'x', 'brief': {'audience': '家庭'}}),
    ('run_skill', {'idempotency_key': 'x', 'skill_id': 'native:template-library', 'input': '查看模板'}),
])
def test_scope_is_checked_before_any_mutation(business_tools, name, args):
    from app.external_agent_models import AgentOAuthGrant
    tools, db, actor = business_tools
    db.get(AgentOAuthGrant, 'grant').scope = 'louxiang:read'
    db.commit()
    actor = replace(actor, scopes=frozenset({'louxiang:read'}))
    with pytest.raises(HTTPException) as error:
        tools.execute_agent_tool(db, actor, name, {'project_id': 'p', **args})
    assert error.value.status_code == 403
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 0


@pytest.mark.parametrize('bad_project', ['p2', 'foreign'])
def test_project_scope_hides_reads_and_rejects_cross_project_reference(business_tools, bad_project):
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'get_project_context', project_id=bad_project)
    assert error.value.status_code == 404


def test_write_requires_durable_idempotency_key_and_unknown_fields_are_rejected(business_tools):
    for args in [{'plan': plan_body()}, {'idempotency_key': 'x'*129, 'plan': plan_body()},
                 {'idempotency_key': 'x', 'plan': plan_body(), 'role': 'owner'},
                 {'idempotency_key': 'x', 'plan': plan_body(verified=True)}]:
        with pytest.raises(HTTPException) as error:
            call(business_tools, 'save_plan', project_id='p', **args)
        assert error.value.status_code == 422


def test_reader_cannot_gain_editor_role_from_write_scope(business_tools):
    from app.core_models import Membership
    tools, db, actor = business_tools
    db.get(Membership, 'member').role = 'viewer'
    db.commit()
    actor = replace(actor, ctx=replace(actor.ctx, role='viewer'))
    with pytest.raises(HTTPException) as error:
        tools.execute_agent_tool(db, actor, 'save_plan', {'project_id': 'p', 'idempotency_key': 'reader', 'plan': plan_body()})
    assert error.value.status_code == 403


def test_unverified_case_cannot_export_a_fabricated_approved_delivery(business_tools):
    from app.business_models import ContentVersion, Release
    db = business_tools[1]
    version = ContentVersion(id='content', tenant_id='t', property_id='p', topic='研究', script='研究稿',
                             created_by='u', status='approved')
    db.add(version)
    db.flush()
    db.add(Release(tenant_id='t', property_id='p', content_version_id='content', status='approved',
                   snapshot={}, snapshot_sha256='f'*64, reviewer_id='u'))
    db.flush()
    result = call(business_tools, 'list_approved_deliveries', project_id='p')
    assert result['deliveries'] == []
    assert result['blocked'][0]['content_version_id'] == 'content'
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'get_approved_delivery', project_id='p', content_version_id='content')
    assert error.value.status_code == 409


def test_run_skill_uses_durable_job_and_never_commits_the_callers_transaction(business_tools):
    from app.job_models import Job
    tools, db, actor = business_tools
    result = call(business_tools, 'run_skill', project_id='p', idempotency_key='skill',
                  skill_id='native:template-library', input='查看可用模板', parameters={'action': 'read'})
    job = db.get(Job, result['job_id'])
    assert job.status == 'queued'
    assert job.kind == 'content_skill'
    assert job.payload['external_agent']['grant_id'] == 'grant'
    assert job.payload['external_agent']['client_id'] == 'client'
    db.rollback()
    assert db.get(Job, result['job_id']) is None
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 0


def test_failed_business_validation_rolls_back_invocation_and_job(business_tools):
    from app.job_models import Job
    tools, db, _ = business_tools
    with pytest.raises(HTTPException):
        call(business_tools, 'run_skill', project_id='p', idempotency_key='bad-skill',
             skill_id='not-a-skill', input='不要创建任务')
    assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 0


@pytest.mark.parametrize('name,args', [('run_skill', {'skill_id': 'native:skill-publish-notify', 'parameters': {'action': 'send'}}),
    ('run_skill', {'skill_id': 'content-calendar', 'parameters': {'action': 'update', 'record': {'status': 'completed'}}})])
def test_generic_skill_cannot_bypass_review_or_explicit_write_tools(business_tools, name, args):
    with pytest.raises(HTTPException) as error:
        call(business_tools, name, project_id='p', idempotency_key='unsafe', **args)
    assert error.value.status_code in (403, 422)


def test_read_tools_do_not_create_change_revisions_and_job_results_do_not_leak_other_project_links(business_tools):
    from app.job_models import Job
    tools, db, _ = business_tools
    db.add(Job(id='job', tenant_id='t', property_id='p', user_id='u', kind='content_skill',
               status='succeeded', payload={'skill_id': 'template-library'}, result={'artifacts': [
                   {'name': 'report.txt', 'url': '/api/projects/p2/assets/secret/file', 'sha256': 'b'*64,
                    'mime_type': 'text/plain', 'byte_size': 10}], 'private_path': '/root/secret'}))
    db.commit()
    result = call(business_tools, 'get_job', project_id='p', job_id='job')
    assert 'p2/assets' not in json.dumps(result)
    assert '/root/secret' not in json.dumps(result)
    call(business_tools, 'list_projects')
    call(business_tools, 'list_references', project_id='p')
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 0


def test_repeated_skill_submission_has_one_job_and_one_invocation(business_tools):
    from app.job_models import Job
    tools, db, _ = business_tools
    args = {'project_id': 'p', 'idempotency_key': 'one-template-task',
            'skill_id': 'native:template-library', 'parameters': {'action': 'read'}}
    first = call(business_tools, 'run_skill', **args)
    db.commit()
    second = call(business_tools, 'run_skill', **args)
    assert second['job_id'] == first['job_id']
    assert second['receipt']['changed'] is False
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 1


@pytest.mark.parametrize('name,args', [('generate_content', {'topic_id': 'layout'}),
    ('render_content', {'content_version_id': 'draft'})])
def test_unverified_case_production_preserves_the_existing_fact_and_permission_gates(business_tools, name, args):
    from app.business_models import ContentVersion
    from app.job_models import Job
    tools, db, _ = business_tools
    db.add(ContentVersion(id='draft', tenant_id='t', property_id='p', topic='研究', script='研究稿', created_by='u'))
    db.commit()
    with pytest.raises(HTTPException) as error:
        call(business_tools, name, project_id='p', idempotency_key='production', **args)
    assert error.value.status_code == 409
    assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 0


def test_generation_checks_actual_team_budget_before_enqueuing(business_tools, monkeypatch):
    from app.config import settings
    from app.agent_models import ModelUsage
    from app.job_models import Job
    tools, db, _ = business_tools
    monkeypatch.setattr(settings, 'app_env', 'production')
    monkeypatch.setattr(settings, 'native_text_backend', 'resident_ppu')
    monkeypatch.setattr(settings, 'native_local_text_daily_token_budget', 10)
    db.add(ModelUsage(tenant_id='t', property_id='p', job_id='prior', model='ppu:model', input_tokens=10, output_tokens=0))
    db.commit()
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'generate_content', project_id='p', idempotency_key='budget', topic_id='layout')
    assert error.value.status_code == 429
    assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 0


def test_cross_project_reference_is_rejected_before_creating_plan(business_tools):
    from app.operations_models import ContentOpportunity, ContentPlan
    tools, db, _ = business_tools
    db.add(ContentOpportunity(id='other-ref', tenant_id='t', property_id='p2', title='隔离参考', kind='competitor',
                             platform='B站', source_url='https://www.bilibili.com/video/BV1i7411e7hP/'))
    db.commit()
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'save_plan', project_id='p', idempotency_key='bad-ref',
             plan=plan_body(reference_id='other-ref'))
    assert error.value.status_code == 404
    assert db.scalar(select(func.count()).select_from(ContentPlan)) == 0


def test_revoked_grant_cannot_replay_a_previous_successful_write(business_tools):
    from app.external_agent_models import AgentOAuthGrant
    from app.db import utcnow
    _, db, _ = business_tools
    args = {'project_id': 'p', 'idempotency_key': 'revoked', 'plan': plan_body()}
    call(business_tools, 'save_plan', **args)
    db.commit()
    db.get(AgentOAuthGrant, 'grant').revoked_at = utcnow()
    db.commit()
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'save_plan', **args)
    assert error.value.status_code == 401


def test_existing_web_job_cannot_be_reassigned_to_external_grant(business_tools):
    from app.core_models import Fact
    from app.job_models import Job
    _, db, _ = business_tools
    db.get(Fact, 'fact').status = 'verified'
    saved = call(business_tools, 'save_plan', project_id='p', idempotency_key='long-plan',
                 plan=plan_body(topic_id='space', planning={'target_seconds': 300, 'skill_ids': []}))
    db.add(Job(id='web-task', tenant_id='t', property_id='p', user_id='u', kind='generate_content',
               status='queued', payload={'plan_id': saved['plan']['id'], 'topic_id': 'space'}))
    db.commit()
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'generate_content', project_id='p', idempotency_key='keep-web-job',
             plan_id=saved['plan']['id'], topic_id='space')
    assert error.value.status_code == 409
    assert 'external_agent' not in db.get(Job, 'web-task').payload


def external_download_client(env):
    """Real bearer authentication over the production download router."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import get_db, utcnow
    from app.external_agent_api import router
    from app.external_agent_models import AgentOAuthCredential
    db = env[1]
    token = 'lx_agent_a_' + 'a'*64
    db.add(AgentOAuthCredential(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                               grant_id='grant', kind='access', expires_at=utcnow()+timedelta(minutes=10)))
    db.commit()
    app = FastAPI()
    app.include_router(router)
    def database():
        yield db
    app.dependency_overrides[get_db] = database
    return TestClient(app, base_url='https://louxiang.example.test'), {'Authorization': 'Bearer ' + token}


def test_job_preview_link_is_downloadable_with_external_bearer_and_preserves_preview_state(business_tools, tmp_path, monkeypatch):
    from app.config import settings
    from app.job_models import Job
    from app.external_agent_models import AgentOAuthGrant
    db = business_tools[1]
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    directory = tmp_path/'t'/'p'/'skill_outputs'/'preview-task'
    directory.mkdir(parents=True)
    payload = b'original study document'
    (directory/'study.txt').write_bytes(payload)
    db.add(Job(id='preview-task', tenant_id='t', property_id='p', user_id='u', kind='content_skill',
               status='succeeded', payload={}, result={'artifacts': [{'name': 'study.txt',
                 'sha256': hashlib.sha256(payload).hexdigest(), 'byte_size': len(payload), 'mime_type': 'text/plain',
                 'url': '/api/projects/p/operations/skills/artifacts/preview-task/study.txt'}]}))
    db.commit()
    client, headers = external_download_client(business_tools)
    value = call(business_tools, 'get_job', project_id='p', job_id='preview-task')['job']['result']['artifacts'][0]
    assert value['delivery_state'] == 'preview_pending_review'
    assert value['agent_download_url'] == 'https://louxiang.example.test/api/agent/projects/p/jobs/preview-task/artifacts/study.txt'
    assert client.get(value['agent_download_url']).status_code == 401
    response = client.get(value['agent_download_url'], headers=headers)
    assert response.status_code == 200, response.text
    assert response.content == payload
    assert response.headers['X-Louxiang-Delivery-State'] == 'preview_pending_review'
    db.get(AgentOAuthGrant, 'grant').project_ids = ['p2']
    db.commit()
    assert client.get(value['agent_download_url'], headers=headers).status_code == 404


def test_approved_tool_links_use_external_download_and_recheck_the_current_release(business_tools, tmp_path, monkeypatch):
    from app import business
    from app.config import settings
    from app.core_models import Asset, Fact, Property
    from app.business_models import ContentVersion
    db, actor = business_tools[1:]
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    project = db.get(Property, 'p')
    project.marketing_confirmed = True
    project.marketing_evidence = 'isolated test fixture permission'
    db.get(Fact, 'fact').status = 'verified'
    image = db.get(Asset, 'asset')
    image.origin_type = 'actual'
    content = ContentVersion(id='approved', tenant_id='t', property_id='p', created_by='u', script='504㎡空间',
        fact_refs=[{'id': 'fact', 'revision': 1}], storyboard=[{'asset_id': 'asset', 'duration': 3, 'caption': '504㎡空间'}])
    db.add(content)
    db.flush()
    payload = b'opaque render fixture; decoding is tested by the renderer suite'
    (tmp_path/'render.mp4').write_bytes(payload)
    render = Asset(id='render', tenant_id='t', property_id='p', name='交付.mp4', kind='video', mime_type='video/mp4',
        storage_key='render.mp4', sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload), rights='authorized',
        rights_evidence='isolated render permission', origin_type='actual', metadata_json={'renderer': 'ffmpeg',
            'content_version_id': content.id, 'content_snapshot_sha256': business.content_snapshot_hash(db, content)})
    db.add(render)
    content.render_asset_id = render.id
    db.flush()
    business.approve_content(db, replace(actor.ctx, role='owner'), 'p', content.id,
                             {'confirmed': True, 'note': 'isolated approval fixture'})
    db.commit()
    client, headers = external_download_client(business_tools)
    one = call(business_tools, 'get_approved_delivery', project_id='p', content_version_id='approved')
    listed = call(business_tools, 'list_approved_deliveries', project_id='p')['deliveries']
    assert len(listed) == 1
    for value in [one, listed[0]]:
        assert value['delivery_state'] == 'approved_current_release'
        assert value['render_url'] == 'https://louxiang.example.test/api/agent/projects/p/contents/approved/delivery/file'
        assert client.get(value['render_url']).status_code == 401
        downloaded = client.get(value['render_url'], headers=headers)
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == payload
    image.rights = 'revoked'
    db.commit()
    assert client.get(one['render_url'], headers=headers).status_code == 409


def archive_job(business_tools, tmp_path, monkeypatch, *, status='succeeded', job_project='p'):
    """A completed server-owned receipt and actual source/output files, without models."""
    from app.config import settings
    from app.core_models import Asset
    from app.job_models import Job
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    db = business_tools[1]
    source = tmp_path/'source.txt'
    source.write_bytes(b'Original production source')
    db.add(Asset(id='archive-source', tenant_id='t', property_id=job_project, name='source.txt',
                 kind='document', storage_key='source.txt', mime_type='text/plain',
                 byte_size=source.stat().st_size, sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                 rights='authorized', rights_evidence='Original source evidence', origin_type='actual',
                 metadata_json={'model_processing_authorized': False}))
    output = tmp_path/'t'/job_project/'skill_outputs'/'archive-job'
    output.mkdir(parents=True)
    raw = b'Completed original production output'
    (output/'study.txt').write_bytes(raw)
    job = Job(id='archive-job', tenant_id='t', property_id=job_project, user_id='u',
              kind='content_skill', status=status,
              payload={'skill_name': 'video-editing', 'asset_ids': ['archive-source']},
              result={'execution_engine': 'housignal_native', 'artifacts': [{
                  'name': 'study.txt', 'byte_size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                  'mime_type': 'text/plain',
                  'url': f'/api/projects/{job_project}/operations/skills/artifacts/archive-job/study.txt'}]})
    db.add(job)
    db.commit()
    return job, raw


def test_archive_completed_production_preserves_real_provenance_and_is_idempotent(business_tools, tmp_path, monkeypatch):
    from app.core_models import Asset
    tools, db, _ = business_tools
    job, raw = archive_job(business_tools, tmp_path, monkeypatch)
    args = {'project_id': 'p', 'job_id': job.id, 'idempotency_key': 'archive-one',
            'names': ['study.txt'], 'tags': ['案例研究'], 'description': '原创制作资料'}
    first = call(business_tools, 'archive_production_artifacts', **args)
    db.commit()
    result = first['data']
    assert result['job_id'] == job.id
    assert result['completed'] is True
    assert result['publication_state'] == 'not_published'
    row = db.get(Asset, result['archived_asset_ids'][0])
    assert (tmp_path/row.storage_key).read_bytes() == raw
    assert row.sha256 == hashlib.sha256(raw).hexdigest()
    assert row.metadata_json['source_job_id'] == job.id
    assert row.metadata_json['source_artifact_name'] == 'study.txt'
    assert row.metadata_json['source_asset_ids'] == ['archive-source']
    assert row.metadata_json['source_rights'][0]['evidence'] == 'Original source evidence'
    assert row.metadata_json['model_processing_authorized'] is False
    assert row.metadata_json['review_required'] is True
    assert row.metadata_json['tags'] == ['案例研究']
    assert any(source['kind'] == 'job' and source['id'] == job.id for source in first['sources'])
    second = call(business_tools, 'archive_production_artifacts', **args)
    assert second['receipt']['changed'] is False
    assert second['data']['archived_asset_ids'] == [row.id]
    third = call(business_tools, 'archive_production_artifacts', **(args | {'idempotency_key': 'archive-two'}))
    db.commit()
    assert third['data']['archived_asset_ids'] == [row.id]
    assert db.scalar(select(func.count()).select_from(Asset)) == 3
    assert len(list((tmp_path/'t'/'p'/'generated').iterdir())) == 1
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 2


@pytest.mark.parametrize('change,expected', [
    ('missing_receipt', 409), ('cross_project', 404), ('failed', 404), ('canceled', 404),
    ('tampered_output', 409), ('revoked_source', 409), ('revoked_grant', 401),
    ('no_production_scope', 403), ('viewer', 403),
])
def test_archive_rejects_untrusted_or_inaccessible_production_without_writes(business_tools, tmp_path, monkeypatch, change, expected):
    from app.core_models import Asset, Membership
    from app.external_agent_models import AgentOAuthGrant
    from app.db import utcnow
    tools, db, actor = business_tools
    job, _ = archive_job(business_tools, tmp_path, monkeypatch,
                         status=change if change in ('failed', 'canceled') else 'succeeded',
                         job_project='p2' if change == 'cross_project' else 'p')
    if change == 'missing_receipt':
        job.result = {'artifacts': job.result['artifacts']}
    elif change == 'tampered_output':
        (tmp_path/'t'/'p'/'skill_outputs'/job.id/'study.txt').write_bytes(b'altered')
    elif change == 'revoked_source':
        db.get(Asset, 'archive-source').rights = 'revoked'
    elif change == 'revoked_grant':
        db.get(AgentOAuthGrant, 'grant').revoked_at = utcnow()
    elif change == 'no_production_scope':
        db.get(AgentOAuthGrant, 'grant').scope = 'louxiang:read louxiang:write'
        actor = replace(actor, scopes=frozenset({'louxiang:read', 'louxiang:write'}))
    elif change == 'viewer':
        db.get(Membership, 'member').role = 'viewer'
        actor = replace(actor, ctx=replace(actor.ctx, role='viewer'))
    db.commit()
    with pytest.raises(HTTPException) as error:
        tools.execute_agent_tool(db, actor, 'archive_production_artifacts', {
            'project_id': 'p', 'job_id': job.id, 'idempotency_key': 'rejected-archive'})
    assert error.value.status_code == expected
    assert db.scalar(select(func.count()).select_from(Asset)) == 2
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 0
    assert not list(tmp_path.glob('t/p/generated/*'))


@pytest.mark.parametrize('field,value', [('lead_id', 'private-client'), ('rights', 'authorized'),
    ('model_processing_authorized', True), ('approved', True)])
def test_external_archive_rejects_customer_authorization_and_review_fields(business_tools, field, value):
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'archive_production_artifacts', project_id='p', job_id='archive-job',
             idempotency_key='no-escalation', **{field: value})
    assert error.value.status_code == 422


def test_invalid_plan_explains_safe_field_path_and_reason_without_input_or_context(business_tools):
    private = 'PRIVATE_INPUT_BEARER_PASSWORD'
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'save_plan', project_id='p', idempotency_key='explain',
             plan=plan_body(planning={'target_seconds': private}, **{private: private}))
    assert error.value.status_code == 422
    detail = error.value.detail
    assert isinstance(detail, dict)
    assert any(item['path'] == 'plan.planning.target_seconds' and item['type'] == 'int_type'
               and item['reason'] for item in detail['errors'])
    assert any(item['type'] == 'extra_forbidden' for item in detail['errors'])
    encoded = json.dumps(detail)
    assert private not in encoded
    assert 'private@example.org' not in encoded
    assert 'ctx' not in encoded
    assert 'input' not in encoded


def test_archive_payload_conflict_and_revoked_grant_cannot_replay_success(business_tools, tmp_path, monkeypatch):
    from app.db import utcnow
    from app.external_agent_models import AgentOAuthGrant
    _, db, _ = business_tools
    job, _ = archive_job(business_tools, tmp_path, monkeypatch)
    args = {'project_id': 'p', 'job_id': job.id, 'idempotency_key': 'archive-replay'}
    first = call(business_tools, 'archive_production_artifacts', **args)
    db.commit()
    with pytest.raises(HTTPException) as conflict:
        call(business_tools, 'archive_production_artifacts', **(args | {'description': 'Changed request'}))
    assert conflict.value.status_code == 409
    db.get(AgentOAuthGrant, 'grant').revoked_at = utcnow()
    db.commit()
    with pytest.raises(HTTPException) as revoked:
        call(business_tools, 'archive_production_artifacts', **args)
    assert revoked.value.status_code == 401
    assert first['data']['archived_asset_ids']
    assert len(list((tmp_path/'t'/'p'/'generated').iterdir())) == 1


def test_invalid_second_archive_document_leaves_no_partial_asset_or_file(business_tools, tmp_path, monkeypatch):
    from app.core_models import Asset
    tools, db, _ = business_tools
    job, _ = archive_job(business_tools, tmp_path, monkeypatch)
    raw = b'{ invalid json }'
    (tmp_path/'t'/'p'/'skill_outputs'/job.id/'bad.json').write_bytes(raw)
    job.result = {**job.result, 'artifacts': [*job.result['artifacts'], {
        'name': 'bad.json', 'byte_size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'mime_type': 'application/json',
        'url': f'/api/projects/p/operations/skills/artifacts/{job.id}/bad.json'}]}
    db.commit()
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'archive_production_artifacts', project_id='p', job_id=job.id,
             idempotency_key='no-partial', names=['study.txt', 'bad.json'])
    assert error.value.status_code == 422
    assert db.scalar(select(func.count()).select_from(Asset)) == 2
    assert db.scalar(select(func.count()).select_from(tools.AgentInvocation)) == 0
    assert not list(tmp_path.glob('t/p/generated/*'))


@pytest.mark.parametrize('names', [['../study.txt'], ['study.txt', 'study.txt'], ['absent.txt'], ['/study.txt']])
def test_external_archive_canonical_filename_validation_prevents_arbitrary_files(business_tools, tmp_path, monkeypatch, names):
    from app.core_models import Asset
    _, db, _ = business_tools
    job, _ = archive_job(business_tools, tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as error:
        call(business_tools, 'archive_production_artifacts', project_id='p', job_id=job.id,
             idempotency_key='safe-names', names=names)
    assert error.value.status_code in (404, 422)
    assert db.scalar(select(func.count()).select_from(Asset)) == 2
    assert not list(tmp_path.glob('t/p/generated/*'))
