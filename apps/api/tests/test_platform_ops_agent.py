"""Platform operations use real project queue paths and bounded safe results."""
from dataclasses import replace
import pytest
from sqlalchemy import select

from test_agent import agent_data


@pytest.mark.parametrize('name,args',[
    ('get_platform_analytics', {'platform':'douyin'}),
    ('refresh_platform_analytics', {'platform':'douyin'}),
    ('search_platform_content', {'platform':'douyin','query':'住宅','days':30}),
    ('get_operation_task', {'job_id':'task-owned'}),
])
def test_new_operating_tools_have_bounded_function_schemas(name,args):
    from app.agent import _validate_args
    _validate_args(name,args)


def test_analytics_question_reads_platform_snapshot_not_general_project_records():
    from app.agent import _query_plan
    plan=_query_plan('查看当前项目抖音账号的粉丝和作品运营数据')
    assert plan['reads']==[('get_platform_analytics', {'platform':'douyin'})]


def test_platform_housing_search_is_not_intercepted_by_account_status_shortcut():
    from app.agent import _query_plan
    assert _query_plan('请搜索当前抖音账号住宅相关的爆款') is None


def test_server_typed_session_marker_is_not_corrupted_by_phone_redaction():
    from app.agent import sanitize
    marker='a'*26+'13800138000'+'b'*27
    result=sanitize({'session_id':marker,'note':'联系13800138000','other':'13800138000'})
    assert result['session_id']==marker
    assert '13800138000' not in result['note']
    assert result['other']!='13800138000'


def test_search_tool_enqueues_current_project_keyword_job_without_model_or_browser(agent_data):
    from app.agent import execute_tool
    from app.job_models import Job
    db,ctx=agent_data
    result=execute_tool(db,ctx,'p1','search_platform_content',{'platform':'douyin','query':'住宅','days':30})
    job=db.get(Job,result['data']['job_id'])
    assert (job.kind,job.property_id,job.tenant_id,job.user_id)==('discovery_search','p1',ctx.tenant_id,ctx.user_id)
    assert job.payload['query']=='住宅' and job.payload['platforms']==['douyin']
    assert job.payload['days']==30 and job.status=='queued'
    assert result['sources'][0]['page']=='studio'


def test_operation_tasks_enforce_project_scope_and_never_return_private_payload(agent_data):
    from app.agent import execute_tool
    from app.jobs import enqueue_job
    from fastapi import HTTPException
    db,ctx=agent_data
    job=enqueue_job(db,ctx,'p1','discovery_search',{'query':'住宅','private_cookie':'never-return-secret'})
    db.flush()
    public=execute_tool(db,ctx,'p1','get_operation_task',{'job_id':job.id})['data']
    assert public['status']=='queued' and 'payload' not in public
    assert 'never-return-secret' not in str(public)
    with pytest.raises(HTTPException):
        execute_tool(db,ctx,'p2','get_operation_task',{'job_id':job.id})


def test_agent_can_find_recent_operation_tasks_without_private_results_or_payload(agent_data):
    from app.agent import execute_tool
    from app.jobs import enqueue_job
    db,ctx=agent_data
    job=enqueue_job(db,ctx,'p1','discovery_search',{'query':'住宅','platforms':['douyin'],'cookie':'private'})
    job.result={'private_cookie':'do-not-list'}
    db.flush()
    data=execute_tool(db,ctx,'p1','list_operation_tasks',{'platform':'douyin','limit':3})['data']
    assert data['tasks'][0]['id']==job.id
    assert 'payload' not in data['tasks'][0] and 'result' not in data['tasks'][0]
    assert 'do-not-list' not in str(data)


def test_read_only_actor_cannot_enqueue_platform_search(agent_data):
    from app.agent import execute_tool
    from fastapi import HTTPException
    db,ctx=agent_data
    with pytest.raises(HTTPException) as error:
        execute_tool(db,replace(ctx,role='sales'),'p1','search_platform_content',{'platform':'douyin','query':'住宅'})
    assert error.value.status_code==403


def test_tool_compaction_preserves_real_source_status_and_async_task_identity():
    from app.agent import _model_tool_result
    result=_model_tool_result('get_operation_task',{'data':{'id':'job-public','status':'succeeded',
        'result':{'discovery_search':{'sources':[{'platform':'douyin','status':'login_required'}],'items':[]}}},
        'sources':[]},include_ids=False)
    assert result['data']['id']=='job-public'
    assert result['data']['result']['discovery_search']['sources'][0]['status']=='login_required'


def test_analytics_is_registered_with_queue_worker_and_authenticated_http_contract():
    from app.jobs import KINDS
    from app.worker import registered_handlers, JOB_ROLES
    from app.main import app
    assert 'platform_analytics' in KINDS
    assert 'platform_analytics' in registered_handlers()
    assert JOB_ROLES['platform_analytics']==['admin','owner','content']
    spec=app.openapi()['paths']
    assert '/api/projects/{pid}/operations/platform-analytics' in spec
    assert '/api/projects/{pid}/operations/platform-accounts/{platform}/credentials' in spec

