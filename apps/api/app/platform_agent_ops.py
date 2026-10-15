"""Bounded platform research and analytics tools for the project operator."""
from fastapi import HTTPException

from .db import utcnow
from .jobs import enqueue_job, job_dict, scoped_job
from .security import require_project
import re

OPS_NAMES = {'get_platform_analytics','refresh_platform_analytics','search_platform_content','get_operation_task','list_operation_tasks'}


def platform_session_constraint(platform, state):
    """Keep only opaque account-generation provenance, never browser material."""
    from .platform_runtime import PLATFORMS
    if platform not in PLATFORMS or not isinstance(state, dict):
        return None
    sid = state.get('session_id')
    return {'platform': platform, 'session_id': sid if isinstance(sid, str) and re.fullmatch(r'[a-f0-9]{64}', sid) else None,
            'loggedIn': state.get('loggedIn') is True, 'scope': 'operations'}


def current_platform_constraint(ctx, pid, platform):
    from .platform_runtime import runtime
    try:
        state = runtime.login_status(ctx.tenant_id, pid, platform)
    except (ValueError, RuntimeError, OSError):
        state = {}
    return platform_session_constraint(platform, state)


def run_agent_ops(db,ctx,pid,name,args):
    require_project(db,ctx,pid)
    page='accounts'
    sessions=[]
    if name=='list_operation_tasks':
        from sqlalchemy import select
        from .job_models import Job
        rows=db.scalars(select(Job).where(Job.tenant_id==ctx.tenant_id,Job.property_id==pid,
            Job.kind.in_(['platform_prepare','platform_publish','platform_analytics','discovery_search','content_skill']))
            .order_by(Job.created_at.desc()).limit(60)).all()
        selected=args.get('platform')
        rows=[job for job in rows if not selected or (job.payload or {}).get('platform')==selected
              or selected in ((job.payload or {}).get('platforms') or [])]
        data={'tasks':[{key:row.get(key) for key in ('id','kind','platform','status','phase','created_at','completed_at')}
              for row in [job_dict(job,db,ctx) for job in rows[:args.get('limit',8)]]],
              'next_step':'选择相关任务后读取 get_operation_task；仅阶段完成不能证明已获得数据或已发布'}
    elif name=='get_platform_analytics':
        from .platform_analytics import analytics_rows
        data={'accounts':analytics_rows(db,ctx,pid,args.get('platform')),
              'note':'当前账号的真实采集快照；未同步或未登录的平台没有可确认指标'}
        for row in data['accounts']:
            sid=row.get('session_id') or (row.get('account') or {}).get('session_id')
            # Bind populated snapshots to their original account, even if the
            # current local session changed between reading rows and provenance.
            state={'session_id':sid,'loggedIn':True} if sid else None
            sessions.append(platform_session_constraint(row['platform'],state) if state else
                            current_platform_constraint(ctx,pid,row['platform']))
    elif name=='refresh_platform_analytics':
        from .platform_analytics import enqueue_platform_analytics
        data=enqueue_platform_analytics(db,ctx,pid,args['platform'])
        data['next_step']='任务已入队，请用 get_operation_task 核对真实采集结果；未完成不能认定已获取数据'
        queued=scoped_job(db,ctx,data['job_id'])
        sessions.append(platform_session_constraint(args['platform'],
            {'session_id':queued.payload.get('session_id'),'loggedIn':True}))
    elif name=='search_platform_content':
        from .discovery import DiscoverySearch,ROLES,enqueue_discovery
        require_project(db,ctx,pid,ROLES)
        request=DiscoverySearch(query=args['query'],platforms=[args['platform']],days=args.get('days',30),
            min_likes=args.get('min_likes',1000),min_views=args.get('min_views',100000),sort=args.get('sort','engagement'),
            account_scope='operations' if args['platform']=='douyin' else 'research')
        job=enqueue_discovery(db,ctx,pid,request)
        data={'job_id':job.id,'job':job_dict(job,db,ctx),
              'next_step':'请用 get_operation_task 核对查询结果。需登录、受阻和缺失指标须如实说明，候选不代表当前爆款'}
        page='studio'
        if args['platform']=='douyin':
            marker=(job.payload or {}).get('operating_session_id')
            sessions.append(platform_session_constraint('douyin',{'session_id':marker,'loggedIn':True}) if marker else
                            current_platform_constraint(ctx,pid,'douyin'))
    elif name=='get_operation_task':
        job=scoped_job(db,ctx,args['job_id'])
        if job.property_id!=pid or job.kind not in {'platform_prepare','platform_publish','platform_analytics','discovery_search','content_skill'}:
            raise HTTPException(404,'当前项目运营任务不存在')
        data=job_dict(job,db,ctx)
        page='studio' if job.kind in {'discovery_search','content_skill'} else 'accounts'
        if job.kind in {'platform_analytics','platform_prepare','platform_publish'}:
            result=job.result or {}
            analytics=result.get('platform_analytics') or {}
            sid=analytics.get('session_id') or (analytics.get('account') or {}).get('session_id') or result.get('session_id') or (job.payload or {}).get('expected_session_id') or (job.payload or {}).get('session_id')
            platform=(job.payload or {}).get('platform')
            sessions.append(platform_session_constraint(platform,{'session_id':sid,'loggedIn':True}) if sid else
                            current_platform_constraint(ctx,pid,platform))
        elif job.kind=='discovery_search' and (job.payload or {}).get('account_scope')=='operations':
            marker=(job.payload or {}).get('operating_session_id')
            sessions.append(platform_session_constraint('douyin',{'session_id':marker,'loggedIn':True}) if marker else
                            current_platform_constraint(ctx,pid,'douyin'))
    else:
        raise ValueError('运营工具不存在')
    return {'data':data,'sources':[{'kind':'page','id':pid,'label':'平台运营数据与任务',
            'page':page,'observed_at':utcnow().isoformat(),
            **({'platform_sessions':[session for session in sessions if session]} if sessions else {})}]}
