"""Project-owned Easel login sessions and approved publication preparation.

Browser profiles remain server-side. Preparing a package never publishes, and
every use of the package rechecks the current approval and project permissions.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import zipfile
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .business import export_content
from .config import settings
from .core_models import Asset
from .db import get_db
from .job_models import Job
from .jobs import enqueue_job, job_dict, scoped_job
from .media import _verified_path
from .operations_skills import _public_text, _public_url
from .platform_runtime import PLATFORMS, runtime
from .security import Context, context_for_user, get_context, require_project

router = APIRouter(prefix='/api/projects/{pid}/operations/platform-accounts', tags=['平台账号'])
from .platform_credentials import router as credentials_router, credentials_metadata
router.include_router(credentials_router)
ROLES = ('owner', 'admin', 'content')
_CHANNELS = {'xiaohongshu': {'小红书'}, 'kuaishou': {'快手'},
             'weixin-channels': {'微信视频号', '视频号'}, 'zhihu': {'知乎'},
             'bilibili': {'B站', 'B 站', '哔哩哔哩'}, 'douyin': {'抖音'},
             'wechat-oa': {'微信公众号', '公众号'}}
_STATE_FIELDS = {'state', 'message', 'qrReady', 'qrTs', 'verified', 'loggedIn', 'name', 'avatar', 'session_id'}


def _platform(platform: str):
    if platform not in PLATFORMS:
        raise HTTPException(404, '平台不存在')
    return PLATFORMS[platform]


def _runtime_call(method, ctx, pid, platform=None, *args, **kwargs):
    if platform is not None:
        _platform(platform)
    try:
        arguments = (ctx.tenant_id, pid) + ((platform,) if platform is not None else ()) + args
        return getattr(runtime, method)(*arguments, **kwargs)
    except ValueError:
        raise HTTPException(422, '平台操作参数无效，请核对后重试') from None
    except (RuntimeError, OSError):
        raise HTTPException(409, '平台会话暂不可用，请查看账号页后重试') from None


def _state(value):
    if not isinstance(value, dict):
        raise HTTPException(409, '平台会话状态暂不可用')
    result = {key: value[key] for key in _STATE_FIELDS if key in value}
    for key in ('state', 'message'):
        if key in result:
            result[key] = _public_text(str(result[key]), 500 if key == 'message' else 40)
    for key in ('qrReady', 'verified', 'loggedIn'):
        result[key] = result.get(key) is True
    result.setdefault('state', 'success' if result['loggedIn'] and result['verified'] else 'unknown')
    if 'name' in result:
        result['name'] = _public_text(str(result['name']), 80)
    if 'avatar' in result:
        result['avatar'] = _public_url(result['avatar'])
    if not isinstance(result.get('qrTs'), (int, float)):
        result['qrTs'] = None
    result['session_id'] = _session_id(result.get('session_id'))
    return result


def _session_id(value):
    return value if isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}',value) else None


def account_rows(db, ctx, pid):
    require_project(db, ctx, pid)
    rows = _runtime_call('list_accounts', ctx, pid)
    if not isinstance(rows, list):
        raise HTTPException(409, '平台账号列表暂不可用')
    found = {row.get('platform'): row for row in rows if isinstance(row, dict)}
    result = []
    for platform, metadata in PLATFORMS.items():
        row = found.get(platform, {})
        result.append({'platform': platform, 'name': metadata['name'],
            'backend': _public_text(str(metadata.get('backend', metadata['name'])), 100),
            'supported': row.get('supported') is True, 'ready': row.get('ready') is True,
            'loggedIn': row.get('loggedIn') is True, 'verified': row.get('verified') is True,
            'note': _public_text(row.get('note', ''), 500), 'scope': 'project',
            'identity_name': _public_text(str(row.get('identity_name') or ''), 80),
            'avatar': _public_url(row.get('avatar')), 'session_id': _session_id(row.get('session_id')),
            'session_policy':'每个项目每个平台使用一个实际运营账号',
            **({'official_api':credentials_metadata(ctx.tenant_id,pid)} if platform=='wechat-oa' else {})})
    return result


@router.get('')
def list_accounts(pid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    return account_rows(db, ctx, pid)


@router.get('/tasks')
def list_tasks(pid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid)
    rows = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind.in_(['platform_prepare', 'platform_publish'])).order_by(Job.created_at.desc()).limit(100)).all()
    result = []
    for job in rows:
        record = job_dict(job, db, ctx)
        payload = job.payload or {}
        platform = payload.get('platform')
        if platform in PLATFORMS:
            record['platform'] = platform
        record['can_verify'] = ctx.role in ROLES and (ctx.role in {'owner', 'admin'} or job.user_id == ctx.user_id)
        for field in ('content_version_id', 'release_id'):
            value = payload.get(field)
            if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
                record[field] = value
        result.append(record)
    return result


@router.post('/{platform}/login')
def start_login(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    return _state(_runtime_call('start_login', ctx, pid, platform))


@router.get('/{platform}/status')
def login_status(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid)
    return _state(_runtime_call('login_status', ctx, pid, platform))


class SmsBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=r'^[0-9]{4,8}$')


@router.post('/{platform}/sms')
def submit_sms(pid: str, platform: str, body: SmsBody,
               ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    return _state(_runtime_call('submit_sms', ctx, pid, platform, body.code))


@router.get('/{platform}/publish-status')
def publication_status(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid)
    return _state(_runtime_call('publish_status', ctx, pid, platform))


@router.post('/{platform}/publish-sms')
def submit_publication_sms(pid: str, platform: str, body: SmsBody,
                           ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    _platform(platform)
    query = select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind == 'platform_publish', Job.status == 'running')
    if ctx.role not in {'owner', 'admin'}:
        query = query.where(Job.user_id == ctx.user_id)
    active = any((job.payload or {}).get('platform') == platform for job in db.scalars(query).all())
    if not active:
        raise HTTPException(409, '当前没有你可验证的运行中平台提交任务')
    # The code goes directly to the private runtime file, never to a Job or model.
    return _state(_runtime_call('submit_publish_sms', ctx, pid, platform, body.code))


@router.get('/{platform}/qr')
def login_qr(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    path = _runtime_call('qr_path', ctx, pid, platform)
    if path is None:
        raise HTTPException(404, '二维码尚未生成或已失效')
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise OSError()
        with path.open('rb') as source:
            if source.read(8) != b'\x89PNG\r\n\x1a\n':
                raise OSError()
    except OSError:
        raise HTTPException(409, '二维码文件暂不可用，请重新获取') from None
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.post('/{platform}/verify')
def verify_account(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    return _state(_runtime_call('whoami', ctx, pid, platform))


@router.post('/{platform}/logout')
def logout_account(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    return _state(_runtime_call('logout', ctx, pid, platform))


class PrepareBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content_version_id: str = Field(min_length=1, max_length=36)


def checked_export(db, ctx, pid, platform, content_version_id):
    require_project(db, ctx, pid, roles=ROLES)
    _platform(platform)
    package = export_content(db, ctx, pid, content_version_id)
    if package['content']['channel'] not in _CHANNELS[platform]:
        raise HTTPException(409, '内容发布渠道与所选平台不一致，请选择对应版本')
    return package


def prepare_publish(db, ctx, pid, platform, content_version_id):
    package = checked_export(db, ctx, pid, platform, content_version_id)
    state=_state(_runtime_call('login_status',ctx,pid,platform))
    job = enqueue_job(db, ctx, pid, 'platform_prepare', {'platform': platform,
        'content_version_id': content_version_id, 'release_id': package['release']['id'],
        'release_sha256': package['release']['snapshot_sha256'],
        'session_id':state['session_id'] if state['loggedIn'] else None})
    return {'job_id': job.id, 'job': job_dict(job, db, ctx)}


@router.post('/{platform}/prepare')
def prepare_account_publish(pid: str, platform: str, body: PrepareBody,
                            ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    result = prepare_publish(db, ctx, pid, platform, body.content_version_id)
    db.commit()
    return result


def _package_path(job, package_id=None):
    package_id = package_id or ((job.result or {}).get('package') or {}).get('id')
    components = (job.tenant_id, job.property_id, job.id, package_id)
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) for value in components):
        raise HTTPException(409, '任务文件标识无效')
    root = settings.storage_root.resolve()
    path = root / 'generated' / 'platform-packages' / job.tenant_id / job.property_id / job.id / package_id / 'publish-package.zip'
    if path.resolve() != path or not path.is_relative_to(root):
        raise HTTPException(409, '任务文件目录不可用')
    return path


def _assert_active(db):
    check = db.info.get('job_cancelled')
    if check and check():
        raise HTTPException(409, '任务已取消或执行权限已失效')


def handle_prepare(db, job):
    with runtime.publish_lock(job.tenant_id,job.property_id,job.payload['platform']):
        return _handle_prepare_locked(db,job)


def _handle_prepare_locked(db, job):
    # Import after router construction: the publisher uses checked_export here.
    from .platform_publish import publication_cover, publication_preview
    ctx = context_for_user(db, job.user_id, job.tenant_id)
    platform = job.payload['platform']
    package = checked_export(db, ctx, job.property_id, platform, job.payload['content_version_id'])
    if (package['release']['id'] != job.payload.get('release_id') or
            package['release']['snapshot_sha256'] != job.payload.get('release_sha256')):
        raise HTTPException(409, '内容审批已改变，请重新准备发布包')
    state=_state(_runtime_call('login_status',ctx,job.property_id,platform))
    bound_session=_session_id(job.payload.get('session_id'))
    if bound_session and (state['session_id']!=bound_session or not state['loggedIn']):
        raise HTTPException(409,'准备期间平台账号已改变，请重新核对账号并准备')
    account={'name':state.get('name',''),'verified':state['verified'],'session_id':bound_session}
    content = package['content']
    render = db.scalar(select(Asset).where(Asset.id == content['render_asset_id'],
        Asset.tenant_id == ctx.tenant_id, Asset.property_id == job.property_id))
    if render is None:
        raise HTTPException(409, '已审批成片不可用')
    media = _verified_path(render)
    preview = publication_preview(package, platform)
    package_id = str(uuid4())
    path = _package_path(job, package_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    db.info.setdefault('created_private_files', []).extend([str(temporary), str(path)])
    article, cover = None, None
    inputs = [str(media)]
    if preview['content_format'] == 'text':
        inputs = []
    elif preview['content_format'] == 'html':
        article = path.parent / 'article.html'
        db.info['created_private_files'].append(str(article))
        article.write_text(preview['body'], encoding='utf-8')
        article.chmod(0o600)
        cover = publication_cover(db, ctx, job.property_id, package, path.parent)
        inputs = [str(article), str(cover)]
    _assert_active(db)
    command = _runtime_call('build_publish_command', ctx, job.property_id, platform,
        title=preview['title'], body=preview['body'], media=inputs, execute=False)
    if not isinstance(command, dict) or command.get('execute') is not False:
        raise HTTPException(409, '发布计划未保持预演状态')
    name = PLATFORMS[platform]['name']
    plan = {'draft_only': command.get('draft_only') is True,
            'steps': ['校验当前内容审批、事实与素材授权', '整理当前版本成片、标题与文案',
                      f'核验{name}账号登录状态', '在账号页确认提交范围后执行平台提交',
                      '核对实际平台回执和公开状态']}
    manifest = {'schema_version': 1, 'project_id': job.property_id, 'platform': platform,
        'content_version_id': content['id'], 'release_id': package['release']['id'],
        'release_sha256': package['release']['snapshot_sha256'], 'title': preview['title'],
        'video_sha256': render.sha256, 'aspect_ratio': content['aspect_ratio'],
        'dry_run': True, 'publication_state': 'not_published', 'plan': plan,
        'publication_preview': preview,'session_id':bound_session,'account':account}
    if cover:
        manifest.update(cover_source='approved_render_first_frame', cover_sha256=_file_digest(cover))
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr('script.txt', content['script'])
            archive.writestr('cover-title.txt', preview['title'])
            archive.write(media, 'video.mp4')
            if article:
                archive.write(article, 'article.html')
            if cover:
                archive.write(cover, 'cover.jpg')
        temporary.chmod(0o600)
        _assert_active(db)
        os.replace(temporary, path)
    except (OSError, zipfile.BadZipFile):
        temporary.unlink(missing_ok=True)
        raise HTTPException(409, '发布包文件生成失败，请重试') from None
    digest = _file_digest(path)
    return {'platform': platform, 'content_version_id': content['id'],
        'release_id': package['release']['id'], 'release_sha256': package['release']['snapshot_sha256'],
        'dry_run': True, 'publication_state': 'not_published', 'plan': plan, 'publication_preview': preview,
        'session_id':bound_session,'account':account,
        'package': {'id': package_id, 'name': 'publish-package.zip', 'sha256': digest, 'byte_size': path.stat().st_size,
                    'download_url': f'/api/projects/{job.property_id}/operations/platform-accounts/tasks/{job.id}/package'}}


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@router.get('/tasks/{jid}/package')
def download_package(pid: str, jid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid)
    job = scoped_job(db, ctx, jid)
    if job.property_id != pid or job.kind != 'platform_prepare':
        raise HTTPException(404, '发布包不存在')
    if job.status != 'succeeded' or not isinstance(job.result.get('package'), dict):
        raise HTTPException(409, '发布包尚未生成')
    package = export_content(db, ctx, pid, job.payload['content_version_id'])
    if (package['release']['id'] != job.result.get('release_id') or
            package['release']['snapshot_sha256'] != job.result.get('release_sha256')):
        raise HTTPException(409, '发布包对应审批已改变，请重新准备')
    path = _package_path(job)
    try:
        if not path.is_file() or _file_digest(path) != job.result['package']['sha256']:
            raise OSError()
    except OSError:
        raise HTTPException(409, '发布包缺失或完整性校验失败，请重新准备') from None
    return FileResponse(path, media_type='application/zip', filename='publish-package.zip',
                        headers={'Cache-Control': 'no-store'})


def run_agent_tool(db, ctx, pid, name, arguments):
    from .platform_agent_ops import platform_session_constraint, current_platform_constraint
    schemas = {'get_platform_accounts': set(), 'check_platform_account': {'platform'},
               'start_platform_login': {'platform'},
               'prepare_platform_publish': {'platform', 'content_version_id'}}
    expected = schemas.get(name)
    if (expected is None or not isinstance(arguments, dict) or set(arguments) != expected or
            any(not isinstance(value, str) or not value or len(value) > 64 for value in arguments.values())):
        raise ValueError('平台操作工具参数无效')
    require_project(db, ctx, pid)
    if name == 'get_platform_accounts':
        data = account_rows(db, ctx, pid)
        sessions = [platform_session_constraint(row['platform'], row) for row in data]
    elif name == 'check_platform_account':
        data = _state(_runtime_call('whoami', ctx, pid, arguments['platform']))
        sessions = [platform_session_constraint(arguments['platform'], data)]
    elif name == 'start_platform_login':
        require_project(db, ctx, pid, roles=ROLES)
        data = _state(_runtime_call('start_login', ctx, pid, arguments['platform']))
        data['next_step'] = '打开账号页，在当前项目中扫码或输入验证码完成登录'
        sessions = [platform_session_constraint(arguments['platform'], data)]
    else:
        data = prepare_publish(db, ctx, pid, arguments['platform'], arguments['content_version_id'])
        queued = scoped_job(db, ctx, data['job_id'])
        sid = queued.payload.get('session_id')
        sessions = [platform_session_constraint(arguments['platform'], {'session_id': sid, 'loggedIn': True})
                    if sid else current_platform_constraint(ctx, pid, arguments['platform'])]
    return {'data': data, 'sources': [{'kind': 'page', 'id': pid, 'label': '平台账号与操作任务', 'page': 'accounts',
        'platform_sessions': [session for session in sessions if session]}]}


registered_handlers = {'platform_prepare': handle_prepare}
