"""Stateless MCP and JSON transports for project-scoped external agents.

Both adapters call the same fixed business tools. OAuth bearer identity is
required even if the caller also has a valid Web session cookie.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import business, core, native_skills
from .config import settings
from .content_planning import _public_data
from .db import get_db
from .external_agent_auth import (AgentIdentity, authenticate_agent, public_origin,
                                  require_agent_project)
from .external_agent_tools import AgentInvocation, TOOL_DEFINITIONS, execute_agent_tool
from .job_models import Job
from .security import Context, get_context, require_project


router = APIRouter(tags=['External agents'])
PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25', '2026-07-28')
MAX_REQUEST_BYTES = 131072
DISTRIBUTION_DIR = Path(__file__).resolve().parents[3] / 'integrations' / 'louxiang-agent' / 'dist'
DISTRIBUTION_NAMES = frozenset({'louxiang.pyz', 'louxiang_agent-0.1.0-py3-none-any.whl',
                                'louxiang-plugin.zip', 'manifest.json'})
_NO_STORE = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}
_SERVER_INFO = {'name': 'louxiang', 'version': '0.1.0'}
_INSTRUCTIONS = '楼响项目工作区。读写同一 Web 数据；制作产物为待审核预览，人工核验、许可和审批需在 Web 完成。'


def _check_origin(request: Request):
    # Native/CLI clients omit Origin. Browser clients must identify a trusted
    # origin; never derive trust from attacker-controlled Host/Forwarded headers.
    origin = request.headers.get('origin')
    allowed = {public_origin(), 'https://chatgpt.com', 'https://chat.openai.com'}
    allowed.update(settings.allowed_origins)
    if origin and origin not in allowed:
        raise HTTPException(403, '外部 Agent 请求来源未授权')


def _identity(request: Request, db: Session, *, scopes=()) -> AgentIdentity:
    _check_origin(request)
    identity = authenticate_agent(db, request.headers.get('authorization', ''), scopes)
    request.state.context = identity.ctx
    request.state.auth_method = 'external_agent'
    return identity


async def _read_json(request: Request):
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_REQUEST_BYTES:
            raise HTTPException(413, '外部 Agent 请求过大')
    def invalid_constant(_):
        raise ValueError('non-finite JSON number')
    return json.loads(raw, parse_constant=invalid_constant)


def _invoke(db, identity, name, arguments):
    tracked_files = db.info.setdefault('created_private_files', [])
    first_file = len(tracked_files)
    committed = False
    try:
        result = execute_agent_tool(db, identity, name, arguments)
        db.commit()
        committed = True
        return result
    except Exception:
        # Domain services deliberately leave the outer transaction to this
        # adapter, so the invocation receipt cannot survive a failed mutation.
        db.rollback()
        raise
    finally:
        # The archive service uses the same private-output tracking as workers.
        # Only this invocation's new files belong to its outer transaction;
        # committed files must never be removed by a later failed invocation.
        pending_files = tracked_files[first_file:]
        del tracked_files[first_file:]
        if not committed:
            root = settings.storage_root.resolve()
            for name in pending_files:
                path = Path(name)
                if path.is_relative_to(root) and path.parent.name == 'generated':
                    path.unlink(missing_ok=True)


def _error(rid, code, message, status=200, data=None):
    detail = {'code': code, 'message': message}
    if data is not None:
        detail['data'] = data
    return JSONResponse({'jsonrpc': '2.0', 'id': rid, 'error': detail},
                        status_code=status, headers=_NO_STORE)


def _result(rid, result, modern=False):
    if modern:
        result = {**result, 'resultType': 'complete',
                  '_meta': {'io.modelcontextprotocol/serverInfo': _SERVER_INFO}}
    return JSONResponse({'jsonrpc': '2.0', 'id': rid, 'result': result}, headers=_NO_STORE)


def _tool_params(params):
    return (isinstance(params, dict) and isinstance(params.get('name'), str)
            and 0 < len(params['name']) <= 80
            and isinstance(params.get('arguments', {}), dict)
            and set(params) <= {'name', 'arguments', '_meta'}
            and ('_meta' not in params or isinstance(params['_meta'], dict)))


@router.post('/mcp')
async def mcp(request: Request, db: Session = Depends(get_db)):
    identity = _identity(request, db)
    header_version = request.headers.get('mcp-protocol-version')
    if header_version and header_version not in PROTOCOL_VERSIONS:
        return _error(None, -32022, '不支持此 MCP 协议版本', status=400,
                      data={'supported': list(PROTOCOL_VERSIONS), 'requested': header_version})
    try:
        message = await _read_json(request)
    except (ValueError, UnicodeError):
        return _error(None, -32700, 'JSON 格式无效', status=400)
    if not isinstance(message, dict):
        return _error(None, -32600, '需要一个 JSON-RPC 请求', status=400)
    rid = message.get('id')
    if (message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str)
            or ('id' in message and not (rid is None or isinstance(rid, (str, int, float)) and not isinstance(rid, bool)))):
        return _error(None, -32600, 'JSON-RPC 请求无效', status=400)
    method = message['method']
    params = message.get('params', {})
    if not isinstance(params, dict):
        return _error(rid, -32602, '方法参数必须为对象')
    meta = params.get('_meta', {})
    if not isinstance(meta, dict):
        return _error(rid, -32602, '_meta 必须为对象')
    body_version = meta.get('io.modelcontextprotocol/protocolVersion')
    modern = header_version == '2026-07-28' or body_version == '2026-07-28'
    if modern:
        if (header_version != body_version or request.headers.get('mcp-method') != method
                or (method == 'tools/call' and request.headers.get('mcp-name') != params.get('name'))):
            return _error(rid, -32020, 'MCP 请求头与请求内容不一致', status=400)
        if not isinstance(meta.get('io.modelcontextprotocol/clientCapabilities', {}), dict):
            return _error(rid, -32602, '客户端能力必须为对象')
    if 'id' not in message:
        # Client notifications are one-way. Never run a business mutation as a
        # notification, where a client cannot receive its durable receipt.
        if not method.startswith('notifications/'):
            return _error(None, -32600, '此方法需要请求 id', status=400)
        return Response(status_code=202, headers=_NO_STORE)
    if method == 'initialize':
        if (not isinstance(params.get('protocolVersion'), str)
                or not isinstance(params.get('capabilities'), dict)
                or not isinstance(params.get('clientInfo'), dict)
                or not isinstance(params['clientInfo'].get('name'), str)
                or not isinstance(params['clientInfo'].get('version'), str)):
            return _error(rid, -32602, 'initialize 参数不完整')
        proposed = params['protocolVersion']
        # The 2026 protocol uses per-request metadata and server/discover. Older
        # clients negotiate only handshake-based versions through initialize.
        version = proposed if proposed in PROTOCOL_VERSIONS[:-1] else PROTOCOL_VERSIONS[-2]
        return _result(rid, {'protocolVersion': version, 'capabilities': {'tools': {'listChanged': False}},
                             'serverInfo': _SERVER_INFO, 'instructions': _INSTRUCTIONS})
    if method == 'server/discover':
        return _result(rid, {'supportedVersions': list(PROTOCOL_VERSIONS),
            'capabilities': {'tools': {}}, 'instructions': _INSTRUCTIONS,
            'ttlMs': 300000, 'cacheScope': 'private'}, modern=modern)
    if method == 'ping':
        return _result(rid, {}, modern=modern)
    if method == 'tools/list':
        if params.get('cursor'):
            return _error(rid, -32602, '工具列表无需分页')
        value = {'tools': TOOL_DEFINITIONS}
        if modern:
            value.update({'ttlMs': 300000, 'cacheScope': 'private'})
        return _result(rid, value, modern=modern)
    if method == 'tools/call':
        if not _tool_params(params):
            return _error(rid, -32602, 'tools/call 参数无效')
        try:
            value = _invoke(db, identity, params['name'], params.get('arguments', {}))
            is_error = False
        except HTTPException as error:
            value = {'error': {'status': error.status_code, 'detail': _public_data(error.detail)}}
            is_error = True
        return _result(rid, {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}],
                             'structuredContent': value, 'isError': is_error}, modern=modern)
    return _error(rid, -32601, '方法不存在', status=404 if modern else 200)


@router.get('/api/agent/tools')
def tools_list(request: Request, db: Session = Depends(get_db)):
    _identity(request, db)
    return JSONResponse({'tools': TOOL_DEFINITIONS}, headers=_NO_STORE)


@router.post('/api/agent/tools/call')
async def tools_call(request: Request, db: Session = Depends(get_db)):
    identity = _identity(request, db)
    try:
        data = await _read_json(request)
    except (ValueError, UnicodeError):
        raise HTTPException(422, '请求必须为有效 JSON 对象') from None
    if not _tool_params(data) or '_meta' in data:
        raise HTTPException(422, '需要 name 与 arguments 对象')
    return JSONResponse(_invoke(db, identity, data['name'], data.get('arguments', {})), headers=_NO_STORE)


@router.get('/api/projects/{pid}/agent-changes')
def agent_changes(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid)
    # Only successful external writes create AgentInvocation rows. Normal Web
    # reads and private conversations cannot modify this opaque project revision.
    rows = db.scalars(select(AgentInvocation).where(AgentInvocation.tenant_id == ctx.tenant_id,
        AgentInvocation.property_id == pid, AgentInvocation.status == 'succeeded')
        .order_by(AgentInvocation.created_at.desc(), AgentInvocation.id.desc()).limit(100)).all()
    invocations, job_ids = [], set()
    for row in rows:
        result = row.result_json or {}
        if (result.get('receipt') or {}).get('changed') is not True:
            continue
        invocations.append((row.id, row.updated_at.isoformat()))
        jid = result.get('job_id')
        if isinstance(jid, str):
            job_ids.add(jid)
    # A long production may outlive many newer plan edits. Keep the latest
    # explicitly tagged native/production jobs in the fingerprint as well.
    recent = db.scalars(select(Job.id).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind.in_(('content_skill', 'generate_content', 'render_content')),
        Job.payload['external_agent']['project_id'].as_string() == pid)
        .order_by(Job.created_at.desc(), Job.id.desc()).limit(20)).all()
    job_ids.update(recent)
    jobs = []
    if job_ids:
        related = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
            Job.id.in_(job_ids)).order_by(Job.id)).all()
        jobs = [(job.id, job.status, job.progress, job.updated_at.isoformat()) for job in related]
    digest = hashlib.sha256(json.dumps({'invocations': invocations, 'jobs': jobs},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return JSONResponse({'revision': digest}, headers=_NO_STORE)


@router.get('/api/agent/downloads/{name:path}')
def client_download(name: str):
    if name not in DISTRIBUTION_NAMES:
        raise HTTPException(404, '安装包不存在')
    path = DISTRIBUTION_DIR / name
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, '安装包不存在')
    media = 'application/json' if name.endswith('.json') else 'application/octet-stream'
    return FileResponse(path, filename=name, media_type=media,
                        headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'public, max-age=300'})


@router.get('/api/agent/projects/{pid}/jobs/{jid}/artifacts/{name:path}')
def artifact_download(pid: str, jid: str, name: str, request: Request, db: Session = Depends(get_db)):
    identity = _identity(request, db, scopes=('louxiang:read',))
    require_agent_project(db, identity, pid, ('louxiang:read',))
    response = native_skills.skill_artifact(pid, jid, name, ctx=identity.ctx, db=db)
    response.headers['X-Louxiang-Delivery-State'] = 'preview_pending_review'
    return response


def _delivery(db, identity, pid, cid):
    require_agent_project(db, identity, pid, ('louxiang:read',))
    # The canonical export revalidates facts, rights, actual render SHA and the
    # exact release snapshot; a caller cannot manufacture an approved status.
    return business.export_content(db, identity.ctx, pid, cid)


@router.get('/api/agent/projects/{pid}/contents/{cid}/delivery')
def approved_delivery(pid: str, cid: str, request: Request, db: Session = Depends(get_db)):
    identity = _identity(request, db, scopes=('louxiang:read',))
    value = _public_data(_delivery(db, identity, pid, cid))
    value['render_url'] = public_origin() + f'/api/agent/projects/{quote(pid, safe="")}/contents/{quote(cid, safe="")}/delivery/file'
    value['delivery_state'] = 'approved_current_release'
    return JSONResponse(value, headers=_NO_STORE)


@router.get('/api/agent/projects/{pid}/contents/{cid}/delivery/file')
def approved_delivery_file(pid: str, cid: str, request: Request, db: Session = Depends(get_db)):
    identity = _identity(request, db, scopes=('louxiang:read',))
    delivery = _delivery(db, identity, pid, cid)
    asset_id = delivery['snapshot']['render']['id']
    response = core.asset_file(pid, asset_id, ctx=identity.ctx, db=db)
    response.headers['X-Louxiang-Delivery-State'] = 'approved_current_release'
    return response
