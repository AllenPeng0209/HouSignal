"""Explicit human-approved publication with a private at-most-once receipt ledger.

External writes cannot participate in a database transaction. The synced attempt
marker is therefore written before invoking a platform, independently of the
worker's fenced business transaction. An unknown attempt requires reconciliation
and never automatically resubmits, including under a different request key.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .business_models import Publication
from .config import settings
from .core_models import Asset, Property
from .db import get_db
from .job_models import Job
from .jobs import job_dict
from .media import _verified_path
from .platform_accounts import checked_export
from .platform_runtime import PLATFORMS, runtime
from .security import Context, context_for_user, get_context, require_project

router = APIRouter(prefix='/api/projects/{pid}/operations/platform-accounts', tags=['平台发布'])
ROLES = ('owner', 'admin', 'content')
_TITLE_LIMITS = {'xiaohongshu':20, 'douyin':30, 'kuaishou':40,
    'weixin-channels':30, 'zhihu':100, 'bilibili':80, 'wechat-oa':64}
_REQUEST = re.compile(r'^[A-Za-z0-9_-]{8,120}$')
_SESSION_ID = re.compile(r'^[0-9a-f]{64}$')
PUBLISH_TIMEOUT_SECONDS = 900


class PublishInterrupted(Exception):
    """An already-started write needs reconciliation after cancellation/timeout."""


def _stop_process_group(process):
    # The group is created by this Popen call; never read a PID from a model,
    # filesystem receipt, platform output or user input.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # Also remove a surviving child that detached its stdout pipe while the
        # parent exited. Reaping the parent alone does not end its owned group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def _run_publisher(db, spec):
    process = subprocess.Popen(spec['command'],cwd=spec['cwd'],env=spec['env'],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,start_new_session=True,umask=0o077)
    deadline = time.monotonic()+PUBLISH_TIMEOUT_SECONDS
    try:
        while True:
            callback = db.info.get('job_cancelled')
            if (callback and callback()) or time.monotonic() >= deadline:
                raise PublishInterrupted('平台提交执行已中断，结果需人工核对')
            try:
                stdout,stderr=process.communicate(timeout=min(.5,max(.01,deadline-time.monotonic())))
                return subprocess.CompletedProcess(spec['command'],process.returncode,stdout,stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        _stop_process_group(process)


class PublishBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content_version_id: str = Field(min_length=1, max_length=100)
    expected_release_id: str = Field(min_length=1, max_length=100)
    expected_session_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: StrictBool
    request_key: str = Field(pattern=r'^[A-Za-z0-9_-]{8,120}$')


def publication_preview(package: dict, platform: str) -> dict:
    """The exact deterministic publisher text, derived only from the release."""
    if platform not in PLATFORMS:
        raise HTTPException(404, '平台不存在')
    content = package['content']
    title = (content.get('cover_title') or content.get('topic') or '').strip()
    body = content.get('script') or ''
    if not isinstance(body, str) or not title:
        raise HTTPException(409, '当前审核版本缺少平台标题或正文')
    length = len(title)
    if platform == 'xiaohongshu':
        # Match the vendored adapter: non-ASCII UTF-16 units count double,
        # including each surrogate of an emoji, and ASCII units count singly.
        encoded = title.encode('utf-16-le')
        weighted = sum(2 if int.from_bytes(encoded[index:index+2], 'little') > 127 else 1
                       for index in range(0,len(encoded),2))
        length = (weighted+1)//2
    if length > _TITLE_LIMITS[platform]:
        raise HTTPException(409, f'当前审核标题超过该平台{_TITLE_LIMITS[platform]}字限制，请修改并重新审批')
    if platform == 'bilibili' and len(body) > 2000:
        raise HTTPException(409, '当前审核正文超过B站2000字限制，请修改并重新审批')
    if platform == 'wechat-oa':
        body = '\n'.join('<p>'+html.escape(paragraph, quote=True)+'</p>'
                         for paragraph in body.splitlines() if paragraph.strip())
    return {'title':title, 'body':body, 'draft_only':platform == 'wechat-oa',
        'content_format':'html' if platform == 'wechat-oa' else ('text' if platform == 'zhihu' else 'video')}


def _digest(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',',':')).encode()).hexdigest()


def _safe_directory(*parts):
    root = settings.storage_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('发布私有目录无效')
        current.mkdir(mode=0o700, exist_ok=True)
        current.chmod(0o700)
    return current


@contextmanager
def _receipt_lock(tenant_id, pid, platform, release_id, *, blocking=True):
    directory = _safe_directory('platform-publish-ledger', _digest(tenant_id, pid))
    receipt = directory / (_digest(platform, release_id)+'.json')
    fd = os.open(receipt.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise HTTPException(409, '该审核版本正在平台提交，请查看现有任务结果') from None
        yield receipt
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_receipt(path):
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 64*1024:
        raise HTTPException(409, '平台提交记录异常，请人工核对平台作品后处理')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('schema_version') != 1:
            raise ValueError()
        return record
    except (OSError, ValueError):
        raise HTTPException(409, '平台提交记录异常，请人工核对平台作品后处理') from None


def _write_receipt(path, record):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.receipt-')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            json.dump(record, target, ensure_ascii=False)
            target.flush(); os.fsync(target.fileno())
        os.replace(temp, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _authorization(payload, tenant_id, pid, user_id):
    keys = ('platform','content_version_id','release_id','release_sha256','request_key','expected_session_id')
    message = json.dumps([tenant_id,pid,user_id]+[payload.get(key) for key in keys],
        ensure_ascii=False, separators=(',',':')).encode()
    return hmac.new(settings.secret_key.encode(), b'platform-human-confirmation\0'+message,
                    hashlib.sha256).hexdigest()


def _verified_account(ctx, pid, platform, expected_session_id):
    try:
        account = runtime.whoami(ctx.tenant_id, pid, platform)
    except (ValueError, RuntimeError, OSError):
        raise HTTPException(409, '平台会话暂不可用，请在账号页重新校验') from None
    if not isinstance(account, dict) or account.get('verified') is not True or account.get('loggedIn') is not True:
        raise HTTPException(409, '平台账号尚未通过当前登录校验，请先登录并校验账号')
    if account.get('session_id') != expected_session_id:
        raise HTTPException(409, '平台账号已切换，请重新准备发布包并确认当前账号')
    return account


def _render_path(db, ctx, pid, package):
    asset = db.scalar(select(Asset).where(Asset.id == package['content']['render_asset_id'],
        Asset.tenant_id == ctx.tenant_id, Asset.property_id == pid))
    if asset is None:
        raise HTTPException(409, '当前审核版本成片不存在')
    return _verified_path(asset)


def _preflight(db, ctx, pid, platform, package):
    preview = publication_preview(package, platform)
    render = _render_path(db, ctx, pid, package)
    if platform == 'wechat-oa':
        if not shutil.which(settings.ffmpeg_binary):
            raise HTTPException(409, '公众号封面生成依赖暂不可用')
    else:
        try:
            spec = runtime.build_publish_command(ctx.tenant_id, pid, platform,
                title=preview['title'], body=preview['body'],
                media=[] if platform == 'zhihu' else [str(render)], execute=False)
            runtime.validate_publish_command(spec, ctx.tenant_id, pid, platform)
        except (ValueError, RuntimeError, OSError):
            raise HTTPException(409, '当前内容格式或平台发布依赖不支持，请重新检查发布包') from None
    return preview


def enqueue_publish(db: Session, ctx: Context, pid: str, platform: str, body: dict) -> Job:
    require_project(db, ctx, pid, roles=ROLES)
    if not isinstance(body, dict) or body.get('confirmed') is not True:
        raise HTTPException(422, '请预览当前审核版本后，由负责人明确确认发布')
    key, cid = body.get('request_key'), body.get('content_version_id')
    if not isinstance(key, str) or not _REQUEST.fullmatch(key) or not isinstance(cid, str) or not cid.strip():
        raise HTTPException(422, '发布请求编号或内容版本无效')
    expected_session_id = body.get('expected_session_id')
    if not isinstance(expected_session_id, str) or not _SESSION_ID.fullmatch(expected_session_id):
        raise HTTPException(422, '请重新准备发布包，并确认当前平台账号后提交')
    package = checked_export(db, ctx, pid, platform, cid)
    if 'expected_release_id' in body and body['expected_release_id'] != package['release']['id']:
        raise HTTPException(409, '审核版本已更新，请重新预览当前发布包并确认')
    _preflight(db, ctx, pid, platform, package)
    # Row locking serializes enqueue transactions in PostgreSQL. The deterministic
    # job primary key also rejects duplicate request races in other databases.
    db.scalar(select(Property).where(Property.id == pid, Property.tenant_id == ctx.tenant_id).with_for_update())
    request_id = str(UUID(_digest(ctx.tenant_id,pid,platform,key)[:32]))
    existing = db.get(Job, request_id)
    if existing:
        if (existing.tenant_id != ctx.tenant_id or existing.property_id != pid or
            (existing.payload or {}).get('release_id') != package['release']['id'] or
            (existing.payload or {}).get('expected_session_id') != expected_session_id):
            raise HTTPException(409, '同一发布请求编号已用于其他审核版本或账号')
        return existing
    with _receipt_lock(ctx.tenant_id, pid, platform, package['release']['id'], blocking=False) as receipt:
        if _read_receipt(receipt) is not None:
            raise HTTPException(409, '该审核版本已有平台提交记录，请先人工核对，避免重复发布')
    active = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind == 'platform_publish', Job.status.in_(['queued','running','succeeded']))).all()
    if any((job.payload or {}).get('platform') == platform and
           (job.payload or {}).get('release_id') == package['release']['id'] for job in active):
        raise HTTPException(409, '该审核版本已有发布任务，请查看现有任务结果')
    _verified_account(ctx, pid, platform, expected_session_id)
    payload = {'platform':platform, 'content_version_id':cid, 'request_key':key,
        'release_id':package['release']['id'], 'release_sha256':package['release']['snapshot_sha256'],
        'expected_session_id':expected_session_id}
    payload['human_confirmation'] = _authorization(payload, ctx.tenant_id, pid, ctx.user_id)
    # Constructing a deterministic id before the shared enqueue flush avoids an
    # implicit commit and provides database-backed request-key idempotency.
    job = Job(id=request_id, tenant_id=ctx.tenant_id, property_id=pid, user_id=ctx.user_id,
        kind='platform_publish', payload=payload, result={}, status='queued', progress=0, attempt=0)
    db.add(job); db.flush()
    return job


@router.post('/{platform}/publish')
def publish_account_content(pid: str, platform: str, body: PublishBody,
    ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    try:
        job = enqueue_publish(db, ctx, pid, platform, body.model_dump())
        db.commit()
    except IntegrityError:
        db.rollback()
        job = enqueue_publish(db, ctx, pid, platform, body.model_dump())
        db.commit()
    return {'job_id':job.id, 'job':job_dict(job, db, ctx)}


def _cancelled(db):
    callback = db.info.get('job_cancelled')
    if callback and callback():
        raise ValueError('平台发布任务已取消或执行租约已失效')


def publication_cover(db, ctx, pid, package, output_dir):
    """Extract one cover from the reviewed visual into a scoped private directory."""
    render = _render_path(db, ctx, pid, package)
    work = Path(output_dir)
    root = settings.storage_root.resolve() / 'generated'
    if work.is_symlink() or not work.resolve().is_relative_to(root):
        raise ValueError('公众号封面输出目录无效')
    current = settings.storage_root.resolve()
    for part in work.relative_to(current).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('公众号封面输出目录无效')
        current.mkdir(mode=0o700, exist_ok=True); current.chmod(0o700)
    cover = work/'cover.jpg'
    _cancelled(db)
    try:
        subprocess.run([settings.ffmpeg_binary,'-nostdin','-y','-i',str(render),'-frames:v','1',
            '-vf','scale=900:-2','-q:v','2',str(cover)], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, check=True, umask=0o077)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(409, '当前审核成片无法生成公众号封面，请重新检查成片') from None
    if not cover.is_file() or cover.is_symlink() or cover.stat().st_size <= 0:
        raise HTTPException(409, '公众号封面尚未生成')
    cover.chmod(0o600)
    db.info.setdefault('created_private_files', []).append(str(cover))
    return cover


def _work_media(db, job, ctx, package, preview):
    render = _render_path(db, ctx, job.property_id, package)
    if preview['content_format'] == 'text':
        return []
    if preview['content_format'] != 'html':
        return [str(render)]
    work = _safe_directory('generated','platform-publish',
        _digest(job.tenant_id,job.property_id,job.id), uuid4().hex)
    article = work/'article.html'
    with article.open('w', encoding='utf-8') as target:
        target.write(preview['body'])
    article.chmod(0o600)
    db.info.setdefault('created_private_files', []).append(str(article))
    cover = publication_cover(db, ctx, job.property_id, package, work)
    return [str(article), str(cover)]


def _public_receipt_url(platform, value):
    if not isinstance(value,str) or len(value)>2048:
        return None
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port or not parsed.hostname:
            return None
    except ValueError:
        return None
    routes = {
        'douyin':{'www.douyin.com':r'/video/[0-9]+/?', 'douyin.com':r'/video/[0-9]+/?'},
        'xiaohongshu':{'www.xiaohongshu.com':r'/(?:explore|discovery/item)/[A-Za-z0-9]+/?',
                        'xiaohongshu.com':r'/(?:explore|discovery/item)/[A-Za-z0-9]+/?'},
        'kuaishou':{'www.kuaishou.com':r'/short-video/[A-Za-z0-9_-]+/?'},
        'weixin-channels':{},
        'zhihu':{'zhuanlan.zhihu.com':r'/p/[0-9]+/?', 'www.zhihu.com':r'/question/[0-9]+/answer/[0-9]+/?'},
        'bilibili':{'www.bilibili.com':r'/video/(?:BV[A-Za-z0-9]+|av[0-9]+)/?'},
        'wechat-oa':{},
    }
    pattern=routes.get(platform,{}).get(parsed.hostname.lower())
    if not pattern or not re.fullmatch(pattern,parsed.path):
        return None
    return urlunsplit(('https',parsed.hostname.lower(),parsed.path,'',''))


def _parse_submission(platform, proc, draft_only):
    uncertain={'status':'reconciliation_required','draft_only':draft_only}
    if proc.returncode != 0:
        return uncertain
    value=None
    for line in reversed((proc.stdout or '').splitlines()):
        try:
            candidate=json.loads(line)
            if isinstance(candidate,dict):
                value=candidate; break
        except (ValueError,TypeError):
            continue
    if not value or value.get('success') is False or value.get('dry_run') is True:
        return uncertain
    status=value.get('status')
    status=status if isinstance(status,str) else None
    if status in {'blocked','failed','error','cancelled','canceled','rejected','timeout',
                  'dry_run','preview','prepared','offline','not_published','login_required'}:
        return uncertain
    if draft_only:
        # The MP adapter's write action only saves a draft; no publication row.
        identifier=value.get('media_id')
        if value.get('success') is not True or not isinstance(identifier,str) or not identifier.strip():
            return uncertain
        return {'status':'draft_saved','draft_only':True}
    if value.get('draft_only') is True or status in {'draft','draft_saved','save_draft'}:
        return uncertain
    if status in {'review_pending','pending_review','under_review'}:
        return {'status':'review_pending','draft_only':False}
    url=_public_receipt_url(platform,value.get('url',value.get('post_url')))
    if status not in {'submitted','published','posted'} and not url:
        return uncertain
    result={'status':'submitted','draft_only':False}
    if url:
        result['url']=url
    return result


def _final_result(db, job, package, record):
    raw=record.get('result') or {'status':'reconciliation_required',
        'draft_only':record.get('platform') == 'wechat-oa'}
    result={key:raw[key] for key in ('status','draft_only') if key in raw}
    result.update(platform=record['platform'], content_version_id=package['content']['id'],
        release_id=package['release']['id'])
    messages={'submitted':'平台提交已结束，作品回执仍需人工核验',
        'review_pending':'平台已接收提交，仍在审核；回执需人工核验',
        'draft_saved':'公众号草稿已保存，请在公众号后台检查草稿',
        'reconciliation_required':'曾开始平台提交，结果尚未确定；请人工核对平台作品，勿重复提交'}
    result['message']=messages.get(result.get('status'),messages['reconciliation_required'])
    url=_public_receipt_url(record['platform'],raw.get('url'))
    if url and not result.get('draft_only') and result.get('status')=='submitted':
        existing=db.scalar(select(Publication).where(Publication.tenant_id==job.tenant_id,
            Publication.property_id==job.property_id, Publication.release_id==package['release']['id'],
            Publication.url==url))
        if existing is None:
            existing=Publication(tenant_id=job.tenant_id,property_id=job.property_id,
                release_id=package['release']['id'],title=package['content']['cover_title'],
                channel=package['content']['channel'],url=url,platform_state='pending',
                receipt_status='unverified',metrics={'D7':None,'D30':None})
            db.add(existing); db.flush()
        result.update(url=url,publication_id=existing.id)
    return result


def handle_publish(db: Session, job: Job):
    payload=job.payload or {}
    signature=payload.get('human_confirmation')
    expected_session_id=payload.get('expected_session_id')
    if (not isinstance(expected_session_id,str) or not _SESSION_ID.fullmatch(expected_session_id) or
        not isinstance(signature,str) or not hmac.compare_digest(signature,
        _authorization(payload,job.tenant_id,job.property_id,job.user_id))):
        raise ValueError('发布任务缺少负责人对当前审核版本的明确确认')
    ctx=context_for_user(db,job.user_id,job.tenant_id)
    platform=payload.get('platform')
    package=checked_export(db,ctx,job.property_id,platform,payload.get('content_version_id'))
    if package['release']['id']!=payload.get('release_id') or package['release']['snapshot_sha256']!=payload.get('release_sha256'):
        raise HTTPException(409,'发布任务所确认的审核版本已改变，请重新预览并确认')
    preview=publication_preview(package,platform)
    _cancelled(db)
    with _receipt_lock(job.tenant_id,job.property_id,platform,package['release']['id']) as receipt:
        prior=_read_receipt(receipt)
        request_hash=_digest(payload['request_key'])
        if prior is not None:
            if prior.get('request_hash')!=request_hash or prior.get('expected_session_id')!=expected_session_id:
                raise HTTPException(409,'该审核版本已有平台提交记录，请先人工核对，避免重复发布')
            return _final_result(db,job,package,prior)
        try:
            with runtime.publish_lock(job.tenant_id,job.property_id,platform):
                _verified_account(ctx,job.property_id,platform,expected_session_id)
                media=_work_media(db,job,ctx,package,preview)
                spec=runtime.build_publish_command(job.tenant_id,job.property_id,platform,
                    title=preview['title'],body=preview['body'],media=media,execute=True)
                runtime.validate_publish_command(spec,job.tenant_id,job.property_id,platform)
                if spec.get('execute') is not True:
                    raise ValueError('发布执行模式无效')
                # Validate authorization and reviewed private bytes again as close
                # as possible to the irreversible external process boundary.
                db.expire_all()
                current=context_for_user(db,job.user_id,job.tenant_id)
                current_package=checked_export(db,current,job.property_id,platform,payload['content_version_id'])
                if current_package['release']['snapshot_sha256']!=payload['release_sha256']:
                    raise HTTPException(409,'发布审核版本已改变')
                _cancelled(db)
                record={'schema_version':1,'platform':platform,'release_id':package['release']['id'],
                    'release_sha256':payload['release_sha256'],'job_id':job.id,
                    'request_hash':request_hash,'expected_session_id':expected_session_id,'state':'attempting'}
                _write_receipt(receipt,record)
                try:
                    proc=_run_publisher(db,spec)
                    outcome=_parse_submission(platform,proc,spec['draft_only'])
                except (OSError,subprocess.SubprocessError,PublishInterrupted):
                    outcome={'status':'reconciliation_required','draft_only':spec['draft_only']}
                record.update(state='finished',result=outcome)
                _write_receipt(receipt,record)
        except RuntimeError:
            raise HTTPException(409,'该平台会话正在使用，请稍后重试') from None
        return _final_result(db,job,package,record)


registered_handlers={'platform_publish':handle_publish}
