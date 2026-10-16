"""Actionable project work with role boundaries and explicit local deadlines."""
from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from .business import (EDIT_ROLES, REVIEW_ROLES, SALES_ROLES, _scope, _inquiry_query,
    list_leads_data, normalize_metrics)
from .business_models import ContentVersion, Inquiry, Publication
from .core import EDIT_ROLES as FACT_EDIT_ROLES, valid_fact
from .core_models import Fact, User
from .job_models import Job
from .secretary_models import ViewingAppointment
from .security import require_project

ZONE=ZoneInfo('Asia/Shanghai')


def now(): return datetime.now(timezone.utc)


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _job_input_identity(job):
    payload=job.payload or {}
    # A kind alone is never an operation identity. Anchor to a business input,
    # then preserve every other input so a changed plan/channel/asset is distinct.
    anchors=('content_version_id','topic_id','plan_id','asset_id','reference_id')
    if not any(isinstance(payload.get(key),str) and payload[key] for key in anchors): return None
    # A renewed OAuth grant changes permission provenance, never the rendering
    # input. Visibility is already bounded by project and job owner below.
    attempt_fields={'resume_from_job_id','request_key','idempotency_key','human_confirmation','external_agent'}
    inputs={key:value for key,value in payload.items() if key not in attempt_fields}
    # Kept private; neither input text nor credentials are serialized to the task list.
    return job.kind,json.dumps(inputs,sort_keys=True,ensure_ascii=True,separators=(',',':'))


def project_tasks_data(db,ctx,pid):
    project=require_project(db,ctx,pid)
    stamp=now().astimezone(ZONE); today=stamp.date(); items=[]

    def add(kind,ident,title,detail,target,due_at=None,owner_id=None,blocked=False,blocker=None):
        owner=db.get(User,owner_id) if owner_id else None
        items.append({'id':f'{kind}:{ident}','kind':kind,'title':title,'detail':detail,
            'due_at':_aware(due_at).astimezone(ZONE).isoformat() if due_at else None,
            'owner_id':owner_id,'owner_name':owner.name if owner else None,
            'blocked':blocked,'blocker':blocker,'target':target})

    if ctx.role in FACT_EDIT_ROLES:
        facts=db.scalars(_scope(db,Fact,ctx,pid).where(Fact.required.is_(True))).all()
        if not facts:
            add('fact_gap','missing','补充项目核心事实','项目还没有可核验的核心事实',{'page':'property'})
        for fact in facts:
            if not valid_fact(fact):
                add('fact_gap',fact.id,f'核对{fact.label}',f'当前状态：{fact.status}；请补齐有效值、单位与资料来源',{'page':'property'})
        if ctx.role in REVIEW_ROLES and (not project.marketing_confirmed or not project.marketing_evidence.strip()):
            add('fact_gap','marketing','确认项目营销授权','请核对营销授权与依据',{'page':'property'})

    if ctx.role in set(EDIT_ROLES+REVIEW_ROLES):
        versions=db.scalars(_scope(db,ContentVersion,ctx,pid).order_by(ContentVersion.version.desc())).all()
        latest={}
        for content in versions: latest.setdefault(content.series_id,content)
        for content in latest.values():
            if content.status in ('rendered','needs_review'):
                add('content_review',content.id,f'审核：{content.cover_title or content.topic}',f'内容 v{content.version} 已可检查',{'page':'studio','contentId':content.id,'studioStage':'review'})
            elif content.status=='needs_revision' and ctx.role in EDIT_ROLES:
                add('content_review',content.id,f'处理审核意见：{content.cover_title or content.topic}',f'内容 v{content.version} 已退回修改',{'page':'studio','contentId':content.id,'studioStage':'script'})
        requests=_scope(db,Job,ctx,pid).where(Job.status.in_(('failed','queued','running','succeeded')))
        if ctx.role not in ('owner','admin'): requests=requests.where(Job.user_id==ctx.user_id)
        jobs=db.scalars(requests.order_by(Job.updated_at.desc())).all()
        recoveries={}
        for job in jobs:
            if job.status not in ('queued','running','succeeded'): continue
            identity=_job_input_identity(job)
            if identity is not None and job.created_at:
                created=_aware(job.created_at)
                if identity not in recoveries or created>recoveries[identity]: recoveries[identity]=created
        for job in jobs:
            if job.status!='failed': continue
            recovery=recoveries.get(_job_input_identity(job))
            if recovery is not None and job.created_at and recovery>_aware(job.created_at): continue
            payload=job.payload or {}; result=job.result or {}
            cid=payload.get('content_version_id') or result.get('content_version_id')
            content=next((c for c in versions if c.id==cid),None)
            target={'page':'studio','jobId':job.id}
            if content: target.update(contentId=content.id,studioStage='review' if content.render_asset_id else 'script')
            label={'generate_content':'内容生成','render_content':'视频制作','illustrate_content':'配图制作','platform_publish':'平台发布'}.get(job.kind,'后台任务')
            add('failed_job',job.id,f'{label}失败','查看任务记录并处理失败原因',target,owner_id=job.user_id)

    if ctx.role in SALES_ROLES:
        inquiries=db.scalars(_inquiry_query(db,ctx,pid).where(Inquiry.qualified.is_(False),Inquiry.withdrawn.is_(False))).all()
        for inquiry in inquiries:
            add('pending_inquiry',inquiry.id,f'确认咨询：{inquiry.name}','核对联系授权、真实需求及项目匹配',{'page':'leads','inquiryId':inquiry.id},owner_id=inquiry.owner_id)
        leads={row['id']:row for row in list_leads_data(db,ctx,pid) if row['stage'] not in ('won','lost','withdrawn')}
        for lead in leads.values():
            deadline=datetime.fromisoformat(lead['next_action_at']) if lead['next_action_at'] else None
            if deadline and deadline.astimezone(ZONE).date()<=today:
                add('followup',lead['id'],f'跟进：{lead["name"]}',lead['next_action'] or '处理客户跟进',{'page':'leads','leadId':lead['id']},deadline,lead['owner_id'])
        for viewing in db.scalars(_scope(db,ViewingAppointment,ctx,pid).where(ViewingAppointment.status.in_(('scheduled','pending_details')))).all():
            if viewing.lead_id not in leads: continue
            day=_aware(viewing.starts_at).astimezone(ZONE).date().isoformat() if viewing.starts_at else viewing.date
            if not day or day>today.isoformat(): continue
            lead=leads[viewing.lead_id]
            details=' · '.join(value for value in (day,viewing.time_window,viewing.location) if value)
            if viewing.status=='pending_details': details+='；请补充预约信息'
            add('viewing',viewing.id,f'带看：{lead["name"]}',details,{'page':'leads','leadId':lead['id']},viewing.starts_at,lead['owner_id'])

    if ctx.role in REVIEW_ROLES:
        pubs=db.scalars(_scope(db,Publication,ctx,pid).where(Publication.receipt_status=='verified',Publication.platform_state=='live')).all()
        for publication in pubs:
            if publication.published_at is None: continue
            for window,days in (('D7',7),('D30',30)):
                deadline=_aware(publication.published_at)+timedelta(days=days)
                if deadline<=stamp and normalize_metrics(publication.metrics)[window] is None:
                    add('metrics',f'{publication.id}:{window}',f'回填{window}：{publication.title}','作品观察窗口已到，请填写实际观察值；未知值可留空',{'page':'reports','publicationId':publication.id,'metricsWindow':window},deadline)

    return {'timezone':'Asia/Shanghai','as_of':stamp.isoformat(),'items':items}
