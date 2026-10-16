"""Scoped queue APIs and durable enqueueing without implicit commits."""
import asyncio
import json
import re
from datetime import timezone
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import get_db, SessionLocal
from .job_models import Job, utcnow
from .security import Context, get_context, require_project

router = APIRouter(prefix="/api", tags=["jobs"])
KINDS = {"chat", "generate_content", "illustrate_content", "extract_asset", "render_content", "content_skill", "content_planning", "discovery_search", "reference_analyze", "platform_prepare", "platform_publish", "platform_analytics"}
TERMINAL = {"succeeded", "failed", "cancelled"}


def enqueue_job(db: Session, ctx: Context, project_id: str, kind: str, payload: dict) -> Job:
    require_project(db, ctx, project_id)
    if kind not in KINDS:
        raise HTTPException(422, "不支持的任务类型")
    job = Job(tenant_id=ctx.tenant_id, property_id=project_id, user_id=ctx.user_id,
              kind=kind, payload=payload, result={}, status="queued", progress=0, attempt=0)
    db.add(job)
    db.flush()
    return job


def job_dict(job: Job, db: Session | None = None, ctx: Context | None = None):
    # Inputs can contain private material and internal snapshot values.
    raw_result = job.result or {}
    result = {k: v for k, v in raw_result.items() if k != 'access_refs'}
    if result.get('preview_text'):
        from .agent import _visible_source, sanitize
        refs = raw_result.get('access_refs')
        valid = (job.kind == 'chat' and job.status == 'running' and db is not None and ctx is not None
                 and isinstance(refs, list) and bool(refs))
        if valid:
            valid = all(isinstance(ref, dict) and _visible_source(db, ctx, job.property_id, ref,
                        check_revision=True) for ref in refs)
        if valid:
            result['preview_text'] = sanitize(result['preview_text'])
        else:
            result.pop('preview_text', None)
    def aware(value):
        return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
    phase = getattr(job, 'phase', None)
    if job.kind == 'chat' and isinstance(phase, str):
        phase = re.sub(r'\bPi(?:[ -]+Agent(?:[ -]+Core)?|[ -]+Core)?', '楼管家', phase, flags=re.IGNORECASE)
    created = aware(job.created_at)
    started = aware(getattr(job, 'started_at', None))
    completed = aware(getattr(job, 'completed_at', None))
    first_token = aware(getattr(job, 'first_token_at', None))
    end = completed or (aware(job.updated_at) if job.status in TERMINAL else utcnow())
    def duration(begin, finish):
        return max(0, round((finish-begin).total_seconds()*1000, 1)) if begin and finish else None
    production_request=None
    payload=job.payload or {}
    def identifier(value):
        return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value) else None
    if job.kind=='generate_content' and payload.get('topic_id') in {'space','light','layout','kitchen','tour'}:
        production_request={'kind':job.kind,'topic_id':payload['topic_id'],'plan_id':identifier(payload.get('plan_id'))}
    elif job.kind in ('render_content','illustrate_content') and identifier(payload.get('content_version_id')):
        production_request={'kind':job.kind,'content_version_id':identifier(payload['content_version_id'])}
    preparation_request=None
    if (job.kind=='platform_prepare' and payload.get('platform') in
            {'douyin','xiaohongshu','kuaishou','weixin-channels','zhihu','bilibili','wechat-oa'}
            and identifier(payload.get('content_version_id'))):
        preparation_request={'platform':payload['platform'],'content_version_id':identifier(payload['content_version_id'])}
    publication_request=None
    if (job.kind in ('platform_prepare','platform_publish') and payload.get('platform') in
            {'douyin','xiaohongshu','kuaishou','weixin-channels','zhihu','bilibili','wechat-oa'}
            and identifier(payload.get('content_version_id')) and identifier(payload.get('release_id'))):
        publication_request={key:payload[key] for key in ('platform','content_version_id','release_id')}
    return {"id": job.id, "property_id": job.property_id, "kind": job.kind,
            "waiting_for_job_id": (identifier(payload.get('depends_on_job_id'))
                if job.kind == 'chat' and job.status == 'queued' else None),
            "reference_id": (job.payload or {}).get('reference_id') if job.kind in {'reference_analyze', 'content_planning'} else None,
            "platform": (job.payload or {}).get('platform') if job.kind in {'platform_prepare', 'platform_publish', 'platform_analytics'} else None,
            "skill_id": (job.payload or {}).get('skill_id') if job.kind == 'content_skill' else None,
            "production_request":production_request,
            "preparation_request":preparation_request,
            "publication_request":publication_request,
            "status": job.status, "result": result, "error": job.error,
            "progress": job.progress, "attempt": job.attempt,
            "phase": phase,
            "started_at": started.isoformat() if started else None,
            "attempt_started_at": getattr(job, 'attempt_started_at', None).isoformat() if getattr(job, 'attempt_started_at', None) else None,
            "completed_at": completed.isoformat() if completed else None,
            "first_token_at": first_token.isoformat() if first_token else None,
            "elapsed_ms": duration(created, end), "queue_ms": duration(created, started),
            "execution_ms": duration(started, end), "first_token_ms": duration(created, first_token),
            "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat()}


def scoped_job(db: Session, ctx: Context, job_id: str) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == ctx.tenant_id))
    if job is None:
        raise HTTPException(404, "任务不存在")
    require_project(db, ctx, job.property_id)
    if job.kind == "chat" and job.user_id != ctx.user_id:
        raise HTTPException(404, "任务不存在")
    return job


@router.get("/projects/{pid}/jobs")
def list_jobs(pid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid)
    rows = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid)
                      .order_by(Job.created_at.desc()).limit(100)).all()
    return [job_dict(j, db, ctx) for j in rows if j.kind != "chat" or j.user_id == ctx.user_id]


@router.get("/jobs/{jid}")
def get_job(jid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    return job_dict(scoped_job(db, ctx, jid), db, ctx)


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    job = scoped_job(db, ctx, jid)
    if job.user_id != ctx.user_id and ctx.role not in {"admin", "owner"}:
        raise HTTPException(403, "只能取消自己创建的任务")
    if job.status not in TERMINAL:
        db.execute(update(Job).where(Job.id == job.id,
            Job.status.in_(["queued", "running"])).values(status="cancelled",
                result={}, phase='已取消', completed_at=utcnow(),
                lease_token=None, lease_until=None, updated_at=utcnow())
            .execution_options(synchronize_session=False))
        # Publish the lease fence before touching the assistant row. The worker
        # may own that row until it observes cancellation through another DB
        # connection; holding the job lock while waiting would strand both.
        db.commit()
    db.refresh(job)
    # A previous request may have committed cancellation but rolled back its
    # cleanup. Retry that idempotently, using the actual persisted terminal
    # status so a concurrent successful completion remains untouched.
    if job.status == "cancelled" and job.kind == "chat":
        from .agent import finalize_chat_messages
        finalize_chat_messages(db, job.id, "cancelled", "本轮查询已取消")
        db.commit()
    db.refresh(job)
    return job_dict(job, db, ctx)


@router.get("/jobs/{jid}/events")
def job_events(jid: str, request: Request, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    scoped_job(db, ctx, jid)
    # A long event connection must not reserve a database connection/transaction.
    db.rollback()

    def read_status():
        with SessionLocal() as poll:
            # Also catches logout/expiry, not only membership revocation.
            current = get_context(request, poll)
            return job_dict(scoped_job(poll, current, jid), poll, current)

    async def events():
        previous = None
        last_keepalive = time.monotonic()
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                break
            # Recheck membership and project scope on every emission, including
            # permission revocation while a long SSE connection is open.
            try:
                item = await asyncio.to_thread(read_status)
            except HTTPException:
                yield 'event: error\ndata: {"detail":"访问权限已失效"}\n\n'
                break
            encoded = json.dumps(item, ensure_ascii=False)
            signature = json.dumps({k: v for k, v in item.items() if k not in {'elapsed_ms', 'execution_ms'}}, ensure_ascii=False)
            if signature != previous:
                yield f"event: status\ndata: {encoded}\n\n"
                previous = signature
            elif time.monotonic() - last_keepalive >= 15:
                yield ": keepalive\n\n"
                last_keepalive = time.monotonic()
            if item["status"] in TERMINAL:
                break
            await asyncio.sleep(.15)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
