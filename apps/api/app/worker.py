"""Persistent leases with fenced commits and permission rechecks.

Handlers must not commit: successful business writes and the fenced job state
transition commit together. Cancellation or lease replacement rolls both back.
"""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import importlib
import logging
import math
import signal
import threading
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, text, update

from .db import SessionLocal
from .config import settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import aliased
from .job_models import Job, utcnow
from .security import context_for_user, require_project

logger = logging.getLogger("housignal.worker")
worker_engine = create_engine(getattr(settings, "worker_database_url", "") or settings.database_url,
                              pool_pre_ping=True, hide_parameters=True)
WorkerSessionLocal = sessionmaker(bind=worker_engine, expire_on_commit=False)


class LostLease(Exception):
    pass


JOB_ROLES = {"chat": None, "extract_asset": ["admin", "owner", "editor", "content", "reviewer"],
             "generate_content": ["admin", "owner", "editor", "content"],
             "illustrate_content": ["admin", "owner", "editor", "content"],
             "content_skill": ["admin", "owner", "content"],
             "content_planning": ["admin", "owner", "content"],
             "discovery_search": ["admin", "owner", "content"],
             "reference_analyze": ["admin", "owner", "content"],
             "platform_prepare": ["admin", "owner", "content"],
             "platform_publish": ["admin", "owner", "content"],
             "platform_analytics": ["admin", "owner", "content"],
             "render_content": ["admin", "owner", "editor", "content"]}
START_PHASES = {"chat": "正在查询项目", "generate_content": "正在准备内容资料",
                "illustrate_content": "正在绘制教学图示",
                "content_skill": "正在准备内容技能",
                "content_planning": "正在准备内容策划",
                "discovery_search": "正在准备住宅内容搜索",
                "reference_analyze": "正在准备原片转写与画面分析",
                "platform_prepare": "正在准备平台发布包",
                "platform_publish": "正在执行已确认的平台任务",
                "platform_analytics": "正在读取账号运营数据",
                "extract_asset": "正在读取素材", "render_content": "正在准备成片"}


def _finalize_chat(db, job, status, error):
    if job.kind != "chat":
        return
    # Queue IDs and owner fields are server-owned records. This internal state
    # transition must also work after membership revocation; no handler or
    # external model access is performed here.
    previous = {key:db.info.get(key) for key in ("tenant_id", "user_id")}
    db.info.update(tenant_id=job.tenant_id, user_id=job.user_id)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.tenant_id', :tenant, true), set_config('app.user_id', :user, true)"),
                   {"tenant":job.tenant_id,"user":job.user_id})
    try:
        from .agent import finalize_chat_messages
        finalize_chat_messages(db, job.id, status, error)
    finally:
        for key, value in previous.items():
            if value is None:
                db.info.pop(key, None)
            else:
                db.info[key] = value
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT set_config('app.tenant_id', :tenant, true), set_config('app.user_id', :user, true)"),
                       {"tenant":previous["tenant_id"] or "","user":previous["user_id"] or ""})


def claim_job(db, now=None, lease_seconds=300, max_attempts=3, kinds=None):
    now = now or utcnow()
    kind_filter = Job.kind.in_(tuple(kinds)) if kinds is not None else True
    # Crashed jobs are automatically reclaimed, but never forever.
    exhausted = db.scalars(select(Job).where(kind_filter, Job.status == "running", Job.lease_until <= now,
        Job.attempt >= max_attempts).with_for_update(skip_locked=True).limit(100)).all()
    for old_job in exhausted:
        error = "任务执行多次中断，请重新发起"
        db.execute(update(Job).where(Job.id == old_job.id, Job.status == "running", Job.lease_until <= now).values(
            status="failed", error=error, result={}, phase="执行失败", completed_at=now,
            lease_token=None, lease_until=None, updated_at=now)
            .execution_options(synchronize_session=False))
        _finalize_chat(db, old_job, "failed", error)
    available = or_(and_(Job.status == "queued", or_(Job.lease_until.is_(None), Job.lease_until <= now)),
                    and_(Job.status == "running", Job.lease_until <= now))
    dependency_id = Job.payload['depends_on_job_id'].as_string()
    dependency = aliased(Job)
    dependency_finished = select(dependency.id).where(dependency.id == dependency_id,
        dependency.tenant_id == Job.tenant_id, dependency.property_id == Job.property_id,
        dependency.status.in_(['succeeded', 'failed', 'cancelled'])).correlate(Job).exists()
    dependency_ready = or_(dependency_id.is_(None), dependency_finished)
    candidate = db.scalar(select(Job).where(kind_filter, available, dependency_ready, Job.attempt < max_attempts)
                          .order_by(Job.created_at, Job.id).with_for_update(skip_locked=True).limit(1))
    if candidate is None:
        db.commit()
        db.expire_all()
        return None
    token = str(uuid4())
    count = db.execute(update(Job).where(Job.id == candidate.id, available,
                                         Job.attempt == candidate.attempt).values(
        status="running", attempt=candidate.attempt + 1, lease_token=token,
        lease_until=now + timedelta(seconds=lease_seconds), error=None,
        progress=0.05, phase=START_PHASES.get(candidate.kind, "正在执行"), result={},
        started_at=func.coalesce(Job.started_at, now), attempt_started_at=now,
        completed_at=None, updated_at=now).execution_options(synchronize_session=False)).rowcount
    db.commit()
    if count != 1:
        return None
    db.expire_all()
    return db.get(Job, candidate.id)


def renew_lease(db, job_id, token, attempt, lease_seconds=300):
    now = utcnow()
    count = db.execute(update(Job).where(Job.id == job_id, Job.status == "running",
        Job.lease_token == token, Job.attempt == attempt, Job.lease_until > now).values(
            lease_until=now + timedelta(seconds=lease_seconds), updated_at=now)
        .execution_options(synchronize_session=False)).rowcount
    db.commit()
    return count == 1


def complete_job(db, job_id, token, attempt, result):
    # All handler writes belong to this same transaction. CAS rejects stale work.
    now = utcnow()
    count = db.execute(update(Job).where(Job.id == job_id, Job.status == "running",
        Job.lease_token == token, Job.attempt == attempt, Job.lease_until > now).values(
            status="succeeded", result=result, error=None, progress=1, phase="已完成", completed_at=now,
            lease_token=None, lease_until=None, updated_at=now)
        .execution_options(synchronize_session=False)).rowcount
    if count != 1:
        db.rollback()
        return False
    completed = db.get(Job, job_id)
    if completed and completed.kind == "chat":
        from .agent import drain_pending_controls
        drain_pending_controls(db, completed)
    db.commit()
    db.expire_all()
    return True


def _safe_error(exc):
    # Provider error adapters expose safe messages. Never persist request bodies,
    # arbitrary exception repr, URL credentials, command stderr, or secrets.
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:300]
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return str(exc)[:300]
    if getattr(exc, "safe_message", None):
        return str(exc.safe_message)[:300]
    return "任务执行失败，请检查资料与模型连接后重试"


def fail_job(db, job_id, token, attempt, error, retry=False):
    db.rollback()
    now = utcnow()
    retrying = retry and attempt < 3
    count = db.execute(update(Job).where(Job.id == job_id, Job.status == "running",
        Job.lease_token == token, Job.attempt == attempt, Job.lease_until > now).values(status="queued" if retrying else "failed", error=error,
            result={}, phase="等待重试" if retrying else "执行失败", completed_at=None if retrying else now,
            lease_token=None, lease_until=now+timedelta(seconds=2**attempt) if retrying else None, updated_at=now)
        .execution_options(synchronize_session=False)).rowcount
    if count == 1:
        job = db.get(Job, job_id)
        if job and job.kind == "chat" and not retrying:
            _finalize_chat(db, job, "failed", error)
    db.commit()
    db.expire_all()
    return count == 1


def _cleanup_outputs(db):
    from pathlib import Path
    root = settings.storage_root.resolve()
    for name in db.info.pop("created_private_files", []):
        path = Path(name).resolve()
        if path.is_relative_to(root) and "/generated/" in str(path):
            path.unlink(missing_ok=True)


def registered_handlers():
    result = {}
    for name in ("app.business", "app.agent", "app.media", "app.story_card_production", "app.native_skills", "app.content_planning", "app.discovery", "app.reference_media", "app.platform_accounts", "app.platform_publish", "app.platform_analytics"):
        module = importlib.import_module(name)
        result.update(getattr(module, "registered_handlers", {}))
    return result


def publish_progress(job_id, token, attempt, *, session_factory=WorkerSessionLocal,
                     progress, stage, preview_text=None, access_refs=None, longform_progress=None):
    """Publish only permission-checked, fenced progress in its own transaction.

    The handler's pending business records and assistant message never enter
    this Session. A partial preview remains a job result until final validation.
    """
    if not isinstance(progress, (float, int)) or not math.isfinite(progress) or not 0 <= progress < 1:
        raise ValueError("任务进度无效")
    if not isinstance(stage, str) or not stage.strip():
        raise ValueError("任务阶段无效")
    if preview_text is not None and not isinstance(preview_text, str):
        raise ValueError("回答预览无效")
    # These references are assembled by the server's scoped business tools.
    # Keep every retrieved source: truncating them weakens the permission fence
    # and an arbitrary count limit rejects otherwise valid larger projects.
    if access_refs is not None and (not isinstance(access_refs, list) or
                                    any(not isinstance(ref, dict) for ref in access_refs)):
        raise ValueError("回答来源无效")
    if longform_progress is not None:
        allowed = {'completed_chapters','total_chapters','completed_shots','total_shots','target_seconds','stage'}
        if not isinstance(longform_progress,dict) or set(longform_progress)-allowed:
            raise ValueError('长视频进度仅允许章节和镜头计数')
        for key,maximum in (('completed_chapters',24),('total_chapters',24),('completed_shots',120),('total_shots',120),('target_seconds',600)):
            value=longform_progress.get(key)
            if value is not None and (type(value) is not int or not 0<=value<=maximum):
                raise ValueError('长视频进度计数无效')
        if any(longform_progress.get(f'completed_{part}',0)>longform_progress.get(f'total_{part}',0) for part in ('chapters','shots')):
            raise ValueError('完成进度不能超过总量')
        label=longform_progress.get('stage')
        if label is not None and (not isinstance(label,str) or len(label)>80):
            raise ValueError('长视频阶段无效')
    now = utcnow()
    with session_factory() as db:
        job = db.scalar(select(Job).where(Job.id == job_id, Job.status == "running", Job.lease_token == token,
                        Job.attempt == attempt, Job.lease_until > now))
        if job is None:
            raise LostLease()
        ctx = context_for_user(db, job.user_id, job.tenant_id)
        require_project(db, ctx, job.property_id, roles=JOB_ROLES.get(job.kind))
        result = dict(job.result or {})
        if longform_progress is not None:
            if job.kind not in {'generate_content','render_content'}:
                raise ValueError('此任务不能写长视频进度')
            result['longform_progress']=dict(longform_progress)
        refs = access_refs if access_refs is not None else result.get("access_refs", [])
        from .agent import _visible_source, sanitize
        if not all(_visible_source(db, ctx, job.property_id, ref, check_revision=True) for ref in refs):
            raise HTTPException(409, "查询期间资料或客户权限发生变化，请重新提问。")
        if preview_text is not None:
            result["preview_text"] = sanitize(preview_text)[:12000]
            result["access_refs"] = refs
        elif access_refs is not None:
            result["access_refs"] = refs
        values = {"progress": max(job.progress, float(progress)), "phase": stage.strip()[:80],
                  "result": result, "updated_at": now}
        if preview_text and job.first_token_at is None:
            values["first_token_at"] = now
        changed = db.execute(update(Job).where(Job.id == job_id, Job.status == "running", Job.lease_token == token,
                             Job.attempt == attempt, Job.lease_until > now).values(**values)
                             .execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            db.rollback()
            raise LostLease()
        db.commit()


def execute_job(job_id, token, attempt, session_factory=WorkerSessionLocal, handlers=None):
    # A separate short-lived heartbeat transaction avoids holding queue-row
    # locks while an expensive provider call or FFmpeg process is running.
    stop = threading.Event()
    lost = threading.Event()
    last_cancel_check = 0.0

    def cancelled():
        nonlocal last_cancel_check
        if lost.is_set():
            return True
        # Streaming calls may ask for cancellation on every small delta. Bound
        # DB reads while still detecting cancellation within 200 milliseconds.
        current = time.monotonic()
        if current-last_cancel_check < .2:
            return False
        last_cancel_check = current
        try:
            with session_factory() as poll:
                active = poll.scalar(select(Job.id).where(Job.id == job_id, Job.status == "running",
                                     Job.lease_token == token, Job.attempt == attempt, Job.lease_until > utcnow()))
            if active is None:
                lost.set()
        except Exception:
            lost.set()
        return lost.is_set()

    def progress_callback(**kwargs):
        if lost.is_set():
            raise LostLease()
        try:
            publish_progress(job_id, token, attempt, session_factory=session_factory, **kwargs)
        except LostLease:
            lost.set()
            raise

    def heartbeat():
        while not stop.wait(20):
            try:
                with session_factory() as hb:
                    if not renew_lease(hb, job_id, token, attempt):
                        lost.set()
                        return
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, name="job-heartbeat", daemon=True)
    thread.start()
    try:
        with session_factory() as db:
            job = db.get(Job, job_id)
            if not job or job.status != "running" or job.lease_token != token or job.attempt != attempt:
                return False
            try:
                ctx = context_for_user(db, job.user_id, job.tenant_id)
                require_project(db, ctx, job.property_id, roles=JOB_ROLES.get(job.kind))
                registry = handlers if handlers is not None else registered_handlers()
                handler = registry.get(job.kind)
                if handler is None:
                    raise ValueError("不支持的任务类型")
                db.info["job_cancelled"] = cancelled
                db.info["job_progress"] = progress_callback
                if job.kind in {"chat", "content_skill", "content_planning", "reference_analyze", "extract_asset"}:
                    from .model_usage_ledger import WorkerUsageRecorder
                    db.info["model_usage_recorder"] = WorkerUsageRecorder(session_factory, job)
                result = handler(db, job)
                # The last permission check also rejects revocation during work.
                db.flush()
                db.expire_all()
                current = context_for_user(db, job.user_id, job.tenant_id)
                require_project(db, current, job.property_id, roles=JOB_ROLES.get(job.kind))
                if lost.is_set():
                    db.rollback()
                    _cleanup_outputs(db)
                    return False
                success = complete_job(db, job_id, token, attempt, result)
                if not success:
                    _cleanup_outputs(db)
                return success
            except Exception as exc:
                message = _safe_error(exc)
                from .provider import ProviderError
                retry = isinstance(exc, ProviderError) and any(term in message for term in ("连接失败", "繁忙", "暂时不可用"))
                fail_job(db, job_id, token, attempt, message, retry=retry)
                _cleanup_outputs(db)
                return False
    finally:
        stop.set()
        thread.join(timeout=1)


def run_worker(stop, session_factory=WorkerSessionLocal, handlers=None):
    """Four bounded slots: two interactive, two background, no shared backlog."""
    registry = handlers if handlers is not None else registered_handlers()

    def lane(kinds):
        while not stop.is_set():
            try:
                with session_factory() as db:
                    job = claim_job(db, kinds=kinds)
                    ticket = (job.id, job.lease_token, job.attempt) if job else None
                if ticket:
                    execute_job(*ticket, session_factory=session_factory, handlers=registry)
                else:
                    stop.wait(.2)
            except Exception:
                # Never log raw exception text, SQL values, payload or secrets.
                logger.error("Worker queue connection unavailable")
                stop.wait(.2)

    lanes = (("chat",), ("chat",), ("generate_content", "illustrate_content", "extract_asset", "render_content", "content_skill", "content_planning", "discovery_search", "reference_analyze", "platform_prepare", "platform_publish", "platform_analytics"),
             ("generate_content", "illustrate_content", "extract_asset", "render_content", "content_skill", "content_planning", "discovery_search", "reference_analyze", "platform_prepare", "platform_publish", "platform_analytics"))
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="job-lane") as executor:
        futures = [executor.submit(lane, kinds) for kinds in lanes]
        for future in futures:
            future.result()


def main():
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Worker ready: 2 interactive slots, 2 background slots")
    run_worker(stop)


if __name__ == "__main__":
    main()
