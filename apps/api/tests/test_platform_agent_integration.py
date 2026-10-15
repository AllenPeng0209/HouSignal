"""Account requests must reach scoped platform tools and the persistent worker."""
import pytest


def test_account_status_request_queries_platform_sessions_instead_of_general_project_summary():
    from app.agent import _query_plan
    plan = _query_plan('请查看当前项目的账号登录状态')
    assert plan is not None
    assert plan['reads'] == [('get_platform_accounts', {})]


def test_account_source_can_open_current_project_accounts_only():
    from app.agent import _visible_source
    assert _visible_source(None, None, 'p1', {'kind': 'page', 'id': 'p1', 'page': 'accounts'})
    assert not _visible_source(None, None, 'p1', {'kind': 'page', 'id': 'p2', 'page': 'accounts'})


@pytest.mark.parametrize('name,args', [
    ('get_platform_accounts', {}),
    ('check_platform_account', {'platform': 'douyin'}),
    ('start_platform_login', {'platform': 'xiaohongshu'}),
    ('prepare_platform_publish', {'platform': 'kuaishou', 'content_version_id': 'approved-content'}),
])
def test_agent_accepts_bounded_platform_actions(name, args):
    from app.agent import _validate_args
    _validate_args(name, args)


@pytest.mark.parametrize('args', [
    {'platform': '../../credentials'}, {'platform': 'douyin', 'tenant_id': 'other'},
    {'platform': 'douyin', 'command': 'sh'},
])
def test_agent_cannot_choose_account_paths_tenants_or_commands(args):
    from app.agent import _validate_args
    with pytest.raises(ValueError):
        _validate_args('start_platform_login', args)


def test_external_publication_is_not_a_model_confirmation_tool():
    from app.agent import _validate_args
    with pytest.raises(ValueError):
        _validate_args('submit_platform_publish', {'platform': 'douyin', 'confirmed': True})


def test_platform_status_survives_model_compaction_without_exposing_credentials():
    from app.agent import _model_tool_result
    result = _model_tool_result('get_platform_accounts', {
        'data': {'accounts': [{'platform': 'douyin', 'name': '抖音', 'loggedIn': False, 'state': 'unknown'}]},
        'sources': [{'kind': 'page', 'id': 'p1', 'page': 'accounts', 'label': '平台账号', 'ref': 'S1'}],
        'observed_at': '2026-10-13T14:00:00Z',
    }, include_ids=False)
    assert result['data']['accounts'][0]['state'] == 'unknown'
    assert result['sources'][0]['page'] == 'accounts'


def test_application_exposes_authenticated_platform_login_contract():
    from app.main import app
    spec = app.openapi()
    assert '/api/projects/{pid}/operations/platform-accounts/{platform}/login' in spec['paths']
    assert '/api/projects/{pid}/operations/platform-accounts/{platform}/publish' in spec['paths']


@pytest.mark.parametrize('kind', ['platform_prepare', 'platform_publish'])
def test_platform_work_can_be_persisted_to_the_real_application_queue(kind):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db import Base
    from app.core_models import Tenant, Property, User
    from app.security import Context
    from app.jobs import enqueue_job
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id='t', name='团队')); db.add(User(id='u',name='编辑',email='test@example.test',password_hash='unused'))
        db.flush(); db.add(Property(id='p',tenant_id='t',name='项目',city='苏州')); db.flush()
        job = enqueue_job(db, Context('u','t','content','编辑','test@example.test',None), 'p', kind, {})
        db.commit()
        assert db.get(type(job), job.id).status == 'queued'
    engine.dispose()


def test_running_platform_task_exposes_platform_for_recovery_without_private_payload():
    from app.job_models import Job, utcnow
    from app.jobs import job_dict
    job = Job(id='job',tenant_id='t',property_id='p',user_id='u',kind='platform_publish',
              status='running',payload={'platform':'douyin','request_key':'private-request'},
              result={},created_at=utcnow(),updated_at=utcnow(),progress=0,attempt=1)
    public = job_dict(job)
    assert public['platform'] == 'douyin'
    assert 'payload' not in public and 'request_key' not in public
