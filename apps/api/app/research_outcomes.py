"""Read actual publication receipts linked through a reference's content series."""
from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .business import normalize_metrics
from .business_models import ContentVersion, Publication, Release
from .db import get_db
from .job_models import Job
from .operations_models import ContentOpportunity, ContentPlan
from .security import Context, get_context, require_project
from .video_breakdown import _TEMPLATES


router = APIRouter(prefix='/api/projects/{pid}/operations/research/references', tags=['参考作品复盘'])


def _scope(model, ctx, pid):
    return model.tenant_id == ctx.tenant_id, model.property_id == pid


def _template(plan):
    snapshot = plan.source_snapshot
    analysis = snapshot.get('reference_analysis') if isinstance(snapshot, dict) else None
    template = analysis.get('reuse_template') if isinstance(analysis, dict) else None
    identity = template.get('id') if isinstance(template, dict) else None
    return identity if isinstance(identity, str) and identity in _TEMPLATES else None


@router.get('/{rid}/outcomes')
def outcomes_get(pid: str, rid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid)
    reference = db.scalar(select(ContentOpportunity).where(ContentOpportunity.id == rid, *_scope(ContentOpportunity, ctx, pid)))
    if reference is None:
        from fastapi import HTTPException
        raise HTTPException(404, '参考不存在或无权访问')
    plans = db.scalars(select(ContentPlan).where(*_scope(ContentPlan, ctx, pid),
        or_(ContentPlan.reference_id == rid, ContentPlan.opportunity_id == rid))
        .order_by(ContentPlan.created_at.desc(), ContentPlan.id.desc()).limit(100)).all()
    current_ids = {plan.content_version_id for plan in plans if plan.content_version_id}
    current = {row.id: row for row in db.scalars(select(ContentVersion).where(
        *_scope(ContentVersion, ctx, pid), ContentVersion.id.in_(current_ids))).all()} if current_ids else {}
    series_plans = {}
    for plan in plans:
        version = current.get(plan.content_version_id)
        if version is not None:
            series_plans.setdefault(version.series_id, []).append(plan)
    plans_by_id = {plan.id: plan for plan in plans}
    publication_rows = []
    if plans:
        # A completed generation owns its series even after editing a plan clears
        # the current pointer or regeneration creates another series. Join that
        # durable ownership directly; do not load unbounded historical job IDs.
        generated_plan_id = Job.payload['plan_id'].as_string()
        publication_rows = db.execute(select(Publication, ContentVersion, generated_plan_id).select_from(Publication)
            .join(Release, Publication.release_id == Release.id)
            .join(ContentVersion, Release.content_version_id == ContentVersion.id)
            .outerjoin(Job, and_(Job.id == ContentVersion.series_id, *_scope(Job, ctx, pid),
                Job.kind == 'generate_content', Job.status == 'succeeded',
                generated_plan_id.in_(plans_by_id)))
            .where(*_scope(Publication, ctx, pid), *_scope(Release, ctx, pid), *_scope(ContentVersion, ctx, pid),
                   or_(ContentVersion.series_id.in_(series_plans), Job.id.is_not(None)))
            .order_by(Publication.created_at.desc(), Publication.id.desc()).limit(200)).all()
    publications, live_plans, recorded_plans = [], set(), set()
    for publication, version, historical_plan_id in publication_rows:
        linked = {plan.id: plan for plan in series_plans.get(version.series_id, [])}
        if historical_plan_id in plans_by_id:
            linked[historical_plan_id] = plans_by_id[historical_plan_id]
        ids = list(linked)
        templates = {_template(plan) for plan in linked.values()}
        template = next(iter(templates)) if len(templates) == 1 else None
        verified_live = publication.receipt_status == 'verified' and publication.platform_state == 'live'
        recorded_plans.update(ids)
        if verified_live:
            live_plans.update(ids)
        publications.append({key: getattr(publication, key) for key in (
            'id', 'title', 'url', 'channel', 'platform_state', 'receipt_status')} | {
            'plan_id': ids[0] if len(ids) == 1 else None, 'plan_ids': ids,
            'content_version_id': version.id, 'content_version': version.version,
            'template_id': template, 'metrics': normalize_metrics(publication.metrics)})
    plan_rows = []
    for plan in plans:
        version = current.get(plan.content_version_id)
        stage = ('published' if plan.id in live_plans else 'publication_recorded' if plan.id in recorded_plans
                 else 'script_ready' if version is not None else 'planned')
        plan_rows.append({'id': plan.id, 'title': plan.title, 'content_id': version.id if version is not None else None,
                          'stage': stage, 'template_id': _template(plan)})
    return {'reference_id': reference.id, 'plans': plan_rows, 'publications': publications,
        'verified_live_count': sum(row['receipt_status'] == 'verified' and row['platform_state'] == 'live' for row in publications),
        'coverage_note': '最多读取最近100个关联计划与200条实际发布回执，包含当前脚本系列及已完成生成任务保留的历史系列。'
            '核实上线数量仅统计已核验且仍在线的回执；未核验或已下线的记录单独保留。'
            '参考与模板按计划当前关联展示；历史任务未保存当时参考快照，不能据此确认每次生成使用的参考或结构。'
            '这些关联不证明参考作品、表达结构与发布效果之间的因果关系。'
            '同一系列关联多个计划时保留全部关联，不推断唯一归属；未观察指标保持为空。'}
