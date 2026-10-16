"""Project-scoped OAuth 2.1 for external MCP clients.

Public clients use PKCE S256, opaque hashed credentials and rotating refresh
credentials. No external client can grant itself a Web role or confirmation.
"""
import base64
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import re
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import settings
from .core_models import Membership, Property, Tenant, User
from .db import get_db, utcnow
from .rate_limit import LoginLimiter
from .security import Context, context_for_user, get_context, require_project
from .external_agent_models import (AgentOAuthAuthorization, AgentOAuthClient,
    AgentOAuthCode, AgentOAuthCredential, AgentOAuthGrant)

class OAuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def wrapped(request):
            try:
                return await handler(request)
            except HTTPException as error:
                # OAuth clients expect RFC 6749 top-level error fields.
                if isinstance(error.detail, dict) and 'error' in error.detail:
                    return JSONResponse(error.detail, status_code=error.status_code, headers=error.headers)
                raise
        return wrapped


router = APIRouter(tags=['External agents'], route_class=OAuthRoute)
PUBLIC_POST_PATHS = frozenset({'/api/agent/oauth/register', '/api/agent/oauth/token', '/api/agent/oauth/revoke'})
SCOPES = frozenset({'louxiang:read', 'louxiang:write', 'louxiang:production', 'offline_access'})
WRITE_ROLES = frozenset({'owner', 'admin', 'content'})
OFFICIAL_CLIENT_IDS = frozenset({'https://chatgpt.com/oauth/client.json', 'https://chatgpt.com/oauth/codex/client.json'})
ACCESS_SECONDS = 900
REFRESH_SECONDS = 30 * 24 * 3600
TOKEN_PATTERN = re.compile(r'lx_agent_[ar]_[A-Za-z0-9_-]{64}')
VERIFIER_PATTERN = re.compile(r'[A-Za-z0-9._~-]{43,128}')
CHALLENGE_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}')
BINDING_COOKIE = 'lx_agent_auth'
registration_limiter = LoginLimiter(settings.secret_key, limit=20)
authorization_limiter = LoginLimiter(settings.secret_key, limit=60)
token_limiter = LoginLimiter(settings.secret_key, limit=120)


@dataclass(frozen=True)
class AgentIdentity:
    ctx: Context
    grant_id: str
    client_id: str
    scopes: frozenset[str]
    project_ids: tuple[str, ...]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def public_origin() -> str:
    configured = getattr(settings, 'external_agent_origin', '')
    if not configured:
        configured = next((value for value in settings.allowed_origins if value.startswith('https://')), '')
    if not configured and settings.app_env != 'production':
        configured = next(iter(settings.allowed_origins), '')
    parsed = urlsplit(configured)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.path not in {'', '/'}
            or parsed.query or parsed.fragment or parsed.scheme not in {'http', 'https'}
            or (parsed.scheme == 'http' and (settings.app_env == 'production' or parsed.hostname not in {'localhost', '127.0.0.1'}))):
        raise HTTPException(503, '外部 Agent 的公开 HTTPS 地址尚未配置')
    return configured.rstrip('/')


def public_resource() -> str:
    return public_origin() + '/mcp'


def bearer_challenge(error: str = 'invalid_token') -> str:
    return f'Bearer resource_metadata="{public_origin()}/.well-known/oauth-protected-resource/mcp", error="{error}"'


def _oauth_error(error='invalid_request', description='OAuth 请求无效', status=400):
    raise HTTPException(status, {'error': error, 'error_description': description}, headers={'Cache-Control': 'no-store'})


def _limit(request: Request, limiter):
    actor = request.client.host if request.client else 'unknown'
    if not limiter.allow(actor):
        raise HTTPException(429, {'error': 'temporarily_unavailable', 'error_description': '请求过于频繁，请稍后重试'},
                            headers={'Retry-After': '60', 'Cache-Control': 'no-store'})


def _origin_required(request: Request):
    if request.headers.get('origin') != public_origin():
        raise HTTPException(403, '授权操作需要可信同源页面')


def _reject_browser_origin(request: Request):
    origin = request.headers.get('origin')
    if origin and origin != public_origin():
        raise HTTPException(403, '请求来源未授权')
    if request.cookies.get('hs_session') and not origin:
        raise HTTPException(403, '浏览器请求需要同源校验')


async def _body(request: Request, form=False) -> dict:
    raw = await request.body()
    if len(raw) > 16384:
        _oauth_error(description='请求过大')
    try:
        if form:
            if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                _oauth_error(description='token 请求必须使用表单编码')
            pairs = parse_qsl(raw.decode('utf-8'), keep_blank_values=True, max_num_fields=24, strict_parsing=True)
            if len(dict(pairs)) != len(pairs):
                _oauth_error(description='参数不能重复')
            return dict(pairs)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        _oauth_error(description='请求格式无效')


def _scopes(raw) -> frozenset[str]:
    if not isinstance(raw, str) or not raw or len(raw) > 200:
        _oauth_error('invalid_scope', '请选择需要的权限')
    values = frozenset(raw.split())
    if not values or not values <= SCOPES or not values.intersection({'louxiang:read', 'louxiang:write', 'louxiang:production'}):
        _oauth_error('invalid_scope', '请求了不支持的权限')
    return values


def _valid_redirect(uri):
    if not isinstance(uri, str) or len(uri) > 2048 or any(ord(c) < 33 for c in uri) or '\\' in uri:
        return False
    try:
        value = urlsplit(uri)
        _ = value.port
        if not value.hostname or value.username is not None or value.password is not None or value.fragment:
            return False
        return value.scheme == 'https' or (value.scheme == 'http' and value.hostname in {'127.0.0.1', 'localhost'})
    except ValueError:
        return False


def _redirect_matches(actual: str, registered: str) -> bool:
    if not _valid_redirect(actual):
        return False
    if actual == registered:
        return True
    a, b = urlsplit(actual), urlsplit(registered)
    # RFC 8252 permits a dynamic listening port only for the same loopback URI.
    return (a.scheme == b.scheme == 'http' and a.hostname == b.hostname and a.hostname in {'127.0.0.1', 'localhost'}
            and a.path == b.path and a.query == b.query and not a.fragment and not b.fragment)


def _fetch_official_client_metadata(url: str) -> dict:
    if url not in OFFICIAL_CLIENT_IDS:
        _oauth_error('invalid_client', '不支持此客户端元数据地址')
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0), follow_redirects=False) as client:
            with client.stream('GET', url, headers={'Accept': 'application/json'}) as response:
                if response.status_code != 200:
                    _oauth_error('invalid_client', '官方客户端元数据暂不可用')
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > 32768:
                        _oauth_error('invalid_client', '客户端元数据过大')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (httpx.HTTPError, ValueError, UnicodeError):
        _oauth_error('invalid_client', '官方客户端元数据暂不可用')


def _get_client(db: Session, client_id: str) -> AgentOAuthClient:
    if not isinstance(client_id, str) or len(client_id) > 200:
        _oauth_error('invalid_client', '客户端标识无效')
    if client_id.startswith(('https:', 'http:')):
        if not getattr(settings, 'external_agent_cimd_enabled', True):
            _oauth_error('invalid_client', '此部署未启用客户端元数据连接，请使用动态注册（DCR）')
        if client_id not in OFFICIAL_CLIENT_IDS:
            _oauth_error('invalid_client', '不支持此客户端元数据地址')
        value = _fetch_official_client_metadata(client_id)
        methods = value.get('token_endpoint_auth_methods_supported', [value.get('token_endpoint_auth_method', 'none')])
        redirects = value.get('redirect_uris')
        codex = client_id == 'https://chatgpt.com/oauth/codex/client.json'
        def official_redirect(uri):
            if not _valid_redirect(uri):
                return False
            parsed = urlsplit(uri)
            return ((parsed.scheme == 'https' and parsed.hostname == 'chatgpt.com')
                    or (codex and parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost'}))
        if (value.get('client_id') != client_id or not isinstance(methods, list) or 'none' not in methods
                or not isinstance(redirects, list) or not redirects or len(redirects) > 16
                or not all(official_redirect(r) for r in redirects)):
            _oauth_error('invalid_client', '官方客户端元数据无效')
        row = db.get(AgentOAuthClient, client_id)
        if not row:
            row = AgentOAuthClient(id=client_id, name=str(value.get('client_name', 'ChatGPT'))[:160], redirect_uris=redirects, source='official_metadata')
            db.add(row)
        else:
            row.name = str(value.get('client_name', 'ChatGPT'))[:160]
            row.redirect_uris = redirects
        db.flush()
        return row
    row = db.get(AgentOAuthClient, client_id)
    if not row:
        _oauth_error('invalid_client', '客户端尚未注册')
    return row


@router.get('/.well-known/oauth-authorization-server')
@router.get('/.well-known/oauth-authorization-server/mcp', include_in_schema=False)
def authorization_metadata():
    origin = public_origin()
    return {'issuer': origin, 'authorization_endpoint': origin + '/api/agent/oauth/authorize',
            'token_endpoint': origin + '/api/agent/oauth/token', 'registration_endpoint': origin + '/api/agent/oauth/register',
            'revocation_endpoint': origin + '/api/agent/oauth/revoke', 'response_types_supported': ['code'],
            'grant_types_supported': ['authorization_code', 'refresh_token'], 'token_endpoint_auth_methods_supported': ['none'],
            'code_challenge_methods_supported': ['S256'], 'scopes_supported': sorted(SCOPES),
            'client_id_metadata_document_supported': getattr(settings, 'external_agent_cimd_enabled', True), 'authorization_response_iss_parameter_supported': True}


@router.get('/.well-known/oauth-protected-resource')
@router.get('/.well-known/oauth-protected-resource/mcp', include_in_schema=False)
@router.get('/mcp/.well-known/oauth-protected-resource', include_in_schema=False)
def resource_metadata():
    return {'resource': public_resource(), 'authorization_servers': [public_origin()], 'scopes_supported': sorted(SCOPES),
            'bearer_methods_supported': ['header'], 'resource_name': '楼响项目工作区'}


@router.post('/api/agent/oauth/register')
async def register(request: Request, db: Session = Depends(get_db)):
    _reject_browser_origin(request)
    _limit(request, registration_limiter)
    data = await _body(request)
    redirects = data.get('redirect_uris')
    grant_types = data.get('grant_types', ['authorization_code'])
    if (not isinstance(redirects, list) or not redirects or len(redirects) > 16
            or not all(_valid_redirect(r) for r in redirects) or len(set(redirects)) != len(redirects)
            or data.get('token_endpoint_auth_method', 'none') != 'none'
            or data.get('response_types', ['code']) != ['code']
            or not isinstance(grant_types, list) or not all(isinstance(g, str) for g in grant_types)
            or 'authorization_code' not in grant_types
            or not set(grant_types) <= {'authorization_code', 'refresh_token'}):
        _oauth_error('invalid_client_metadata', '仅支持安全回调地址和 PKCE 公共客户端')
    name = data.get('client_name', '外部 Agent')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
        _oauth_error('invalid_client_metadata', '客户端名称无效')
    cid = 'lx_client_' + secrets.token_urlsafe(24)
    row = AgentOAuthClient(id=cid, name=name.strip(), redirect_uris=redirects)
    db.add(row)
    db.commit()
    return JSONResponse({'client_id': cid, 'client_id_issued_at': int(utcnow().timestamp()), 'client_name': row.name,
        'redirect_uris': redirects, 'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code']}, status_code=201, headers={'Cache-Control': 'no-store'})


def _authorization_page(handle: str, client: AgentOAuthClient, scopes: frozenset[str], nonce: str):
    labels = {'louxiang:read': '读取选定项目的事实、参考、策划、素材和任务进度',
              'louxiang:write': '创建或修改内容策划与草稿（前端同步更新）',
              'louxiang:production': '提交制作任务（可能产生模型或制作费用）',
              'offline_access': '保持连接，允许刷新凭证；可随时撤销'}
    permissions = ''.join(f'<label><input type="checkbox" name="scope" value="{s}" checked> {html.escape(labels[s])}</label>' for s in sorted(scopes))
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>连接楼响</title>
<style nonce="NONCE">body{font:16px/1.7 system-ui;background:#f4f7fb;color:#173047;margin:0;padding:32px}main{max-width:620px;margin:auto;background:white;padding:32px;border-radius:18px}h1{font-size:26px}label{display:block;margin:12px 0}input[type=email],input[type=password]{box-sizing:border-box;width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:8px}button{background:#2457ed;color:white;border:0;border-radius:8px;padding:12px 18px;cursor:pointer}button:disabled{opacity:.5}small{color:#64748b}#message{color:#9f2343;white-space:pre-wrap}[hidden]{display:none!important}</style></head><body><main>
<h1>连接楼响工作区</h1><p><strong>CLIENT</strong> 请求访问你选择的项目。</p><p><small>权限受你的当前角色约束，不包含客户隐私、事实确认、营销授权、审核通过或对外发布。</small></p>
<form id="login" hidden><label>工作邮箱<input name="email" type="email" autocomplete="username" required></label><label>密码<input name="password" type="password" autocomplete="current-password" required></label><button>登录楼响</button></form>
<form id="consent" hidden><p id="account"></p><h2>选择项目</h2><div id="projects"></div><small>只授权勾选的项目，未勾选的项目不可访问。</small><h2>允许的操作</h2>PERMISSIONS<button id="connect" disabled>授权并连接</button></form><p id="message" role="status"></p></main>
<script nonce="NONCE">
const authorizationRequest = HANDLE;
const loginForm=document.getElementById('login'), consentForm=document.getElementById('consent'), message=document.getElementById('message');
async function jsonFetch(url, options={}) { const response=await fetch(url,{credentials:'same-origin',...options}); const data=await response.json(); if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'操作未完成，请重新检查权限或刷新页面。'); return data; }
async function loadWorkspace(){try{const account=await jsonFetch('/api/auth/me');const projects=await jsonFetch('/api/projects');document.getElementById('account').textContent=account.user.name+' · '+account.organization.name;const list=document.getElementById('projects');list.replaceChildren();for(const p of projects){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.name='project';input.value=p.id;label.append(input,document.createTextNode(' '+p.name));list.append(label);}loginForm.hidden=true;consentForm.hidden=false;updateButton();}catch(e){loginForm.hidden=false;consentForm.hidden=true;}}
function updateButton(){document.getElementById('connect').disabled=!document.querySelector('input[name=project]:checked')||!document.querySelector('input[name=scope]:checked');}
consentForm.addEventListener('change',updateButton);
loginForm.addEventListener('submit',async e=>{e.preventDefault();message.textContent='';const data=new FormData(loginForm);try{await jsonFetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:data.get('email'),password:data.get('password')})});loginForm.reset();await loadWorkspace();}catch(error){message.textContent=error.message;}});
consentForm.addEventListener('submit',async e=>{e.preventDefault();const button=document.getElementById('connect');button.disabled=true;message.textContent='';try{const result=await jsonFetch('/api/agent/oauth/consent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:authorizationRequest,project_ids:[...document.querySelectorAll('input[name=project]:checked')].map(x=>x.value),scopes:[...document.querySelectorAll('input[name=scope]:checked')].map(x=>x.value)})});window.location.assign(result.redirect_url);}catch(error){message.textContent=error.message;updateButton();}});
loadWorkspace();
</script></body></html>'''.replace('NONCE', nonce).replace('PERMISSIONS', permissions).replace('HANDLE', json.dumps(handle)).replace('CLIENT', html.escape(client.name))


@router.get('/api/agent/oauth/authorize')
def authorize(request: Request, db: Session = Depends(get_db)):
    _limit(request, authorization_limiter)
    pairs = list(request.query_params.multi_items())
    if len(dict(pairs)) != len(pairs):
        _oauth_error(description='参数不能重复')
    params = dict(pairs)
    client = _get_client(db, params.get('client_id', ''))
    redirect = params.get('redirect_uri', '')
    if not any(_redirect_matches(redirect, uri) for uri in client.redirect_uris):
        _oauth_error(description='回调地址与注册信息不一致')
    if params.get('response_type') != 'code' or params.get('code_challenge_method') != 'S256':
        _oauth_error(description='必须使用 authorization code 和 PKCE S256')
    challenge = params.get('code_challenge', '')
    state = params.get('state', '')
    if not CHALLENGE_PATTERN.fullmatch(challenge) or not state or len(state) > 1024 or any(ord(c) < 32 for c in state):
        _oauth_error(description='PKCE 或 state 参数无效')
    if params.get('resource') != public_resource():
        _oauth_error('invalid_target', '必须请求楼响 MCP 资源')
    scopes = _scopes(params.get('scope', ''))
    handle, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(AgentOAuthAuthorization(request_hash=_hash(handle), binding_hash=_hash(binding), client_id=client.id,
        redirect_uri=redirect, resource=public_resource(), scope=' '.join(sorted(scopes)), state=state,
        challenge=challenge, expires_at=utcnow() + timedelta(minutes=10)))
    db.commit()
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(_authorization_page(handle, client, scopes, nonce), headers={
        'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
        'Content-Security-Policy': f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
    response.set_cookie(BINDING_COOKIE, binding, max_age=600, httponly=True, secure=public_origin().startswith('https:'), samesite='lax', path='/api/agent/oauth')
    return response


@router.post('/api/agent/oauth/consent')
async def consent(request: Request, db: Session = Depends(get_db)):
    _origin_required(request)
    # Authorization is an explicit browser action, never an external bearer action.
    if 'authorization' in request.headers:
        raise HTTPException(401, '请在楼响授权页面登录')
    ctx = get_context(request, db)
    data = await _body(request)
    handle = data.get('request_id', '')
    if not isinstance(handle, str) or not 20 <= len(handle) <= 100:
        _oauth_error(description='授权请求已失效，请重新连接')
    pending = db.scalar(select(AgentOAuthAuthorization).where(AgentOAuthAuthorization.request_hash == _hash(handle)).with_for_update())
    if not pending or pending.used_at or _aware(pending.expires_at) <= utcnow():
        _oauth_error(description='授权请求已失效，请重新连接')
    binding = request.cookies.get(BINDING_COOKIE, '')
    if not binding or not secrets.compare_digest(_hash(binding), pending.binding_hash):
        raise HTTPException(403, '授权页面绑定无效，请重新连接')
    projects = data.get('project_ids')
    selected = data.get('scopes')
    if (not isinstance(projects, list) or not projects or len(projects) > 100 or not all(isinstance(p, str) and len(p) <= 36 for p in projects)
            or len(projects) != len(set(projects)) or not isinstance(selected, list) or not all(isinstance(s, str) for s in selected)):
        _oauth_error(description='请明确选择项目和权限')
    scopes = _scopes(' '.join(selected))
    if not scopes <= _scopes(pending.scope):
        _oauth_error('invalid_scope', '不能扩大本次授权请求的权限')
    if scopes.intersection({'louxiang:write', 'louxiang:production'}) and ctx.role not in WRITE_ROLES:
        raise HTTPException(403, '当前角色没有内容修改或制作权限')
    for pid in projects:
        require_project(db, ctx, pid)
    used = db.execute(update(AgentOAuthAuthorization).where(AgentOAuthAuthorization.id == pending.id,
        AgentOAuthAuthorization.used_at.is_(None)).values(used_at=utcnow()))
    if used.rowcount != 1:
        _oauth_error(description='授权请求已使用')
    now = utcnow()
    row = AgentOAuthGrant(organization_id=ctx.tenant_id, user_id=ctx.user_id, client_id=pending.client_id,
        project_ids=projects, scope=' '.join(sorted(scopes)), resource=pending.resource, expires_at=now + timedelta(seconds=REFRESH_SECONDS))
    db.add(row)
    db.flush()
    code = secrets.token_urlsafe(48)
    db.add(AgentOAuthCode(code_hash=_hash(code), grant_id=row.id, client_id=row.client_id,
        redirect_uri=pending.redirect_uri, resource=pending.resource, challenge=pending.challenge,
        expires_at=now + timedelta(minutes=5)))
    db.commit()
    parsed = urlsplit(pending.redirect_uri)
    query = parse_qsl(parsed.query, keep_blank_values=True) + [('code', code), ('state', pending.state), ('iss', public_origin())]
    response = JSONResponse({'redirect_url': urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ''))}, headers={'Cache-Control': 'no-store'})
    response.delete_cookie(BINDING_COOKIE, path='/api/agent/oauth')
    return response


def _live_identity(db: Session, row: AgentOAuthGrant, required_scopes=()) -> AgentIdentity:
    if not row or row.revoked_at or _aware(row.expires_at) <= utcnow() or row.resource != public_resource():
        raise HTTPException(401, '外部 Agent 连接已失效', headers={'WWW-Authenticate': bearer_challenge()})
    # Long-lived workers may already hold ORM identity-map entries. Refresh the
    # security records explicitly before the canonical Context establishes RLS.
    db.scalar(select(User).where(User.id == row.user_id).execution_options(populate_existing=True))
    db.scalar(select(Membership).where(Membership.user_id == row.user_id,
        Membership.tenant_id == row.organization_id).execution_options(populate_existing=True))
    ctx = context_for_user(db, row.user_id, row.organization_id)
    scopes = frozenset(row.scope.split())
    if not set(required_scopes) <= scopes:
        raise HTTPException(403, '外部 Agent 未获得此操作权限', headers={'WWW-Authenticate': bearer_challenge('insufficient_scope')})
    if set(required_scopes).intersection({'louxiang:write', 'louxiang:production'}) and ctx.role not in WRITE_ROLES:
        raise HTTPException(403, '当前角色没有内容修改或制作权限')
    projects = db.scalars(select(Property.id).where(Property.tenant_id == ctx.tenant_id, Property.id.in_(row.project_ids))).all()
    allowed = tuple(p for p in row.project_ids if p in projects and (ctx.project_ids is None or p in ctx.project_ids))
    return AgentIdentity(replace(ctx, project_ids=list(allowed)), row.id, row.client_id, scopes, allowed)


def authenticate_agent(db: Session, authorization: str, required_scopes: tuple = ()) -> AgentIdentity:
    scheme, separator, token = authorization.partition(' ')
    if not separator or scheme.lower() != 'bearer' or not TOKEN_PATTERN.fullmatch(token) or not token.startswith('lx_agent_a_'):
        raise HTTPException(401, '外部 Agent 登录凭证无效', headers={'WWW-Authenticate': bearer_challenge()})
    credential = db.scalar(select(AgentOAuthCredential).where(AgentOAuthCredential.token_hash == _hash(token), AgentOAuthCredential.kind == 'access'))
    if not credential or _aware(credential.expires_at) <= utcnow():
        raise HTTPException(401, '外部 Agent 登录已过期', headers={'WWW-Authenticate': bearer_challenge()})
    return _live_identity(db, db.get(AgentOAuthGrant, credential.grant_id, populate_existing=True), required_scopes)


def require_agent_project(db: Session, identity: AgentIdentity, pid: str, required_scopes: tuple = ()):
    fresh = _live_identity(db, db.get(AgentOAuthGrant, identity.grant_id, populate_existing=True), required_scopes)
    if fresh.client_id != identity.client_id or fresh.ctx.user_id != identity.ctx.user_id or fresh.ctx.tenant_id != identity.ctx.tenant_id:
        raise HTTPException(401, '外部 Agent 身份绑定无效')
    return require_project(db, fresh.ctx, pid)


def _issue_tokens(db: Session, row: AgentOAuthGrant, scope=None):
    value = {'token_type': 'Bearer', 'expires_in': ACCESS_SECONDS, 'scope': scope or row.scope, 'resource': row.resource}
    access = 'lx_agent_a_' + secrets.token_urlsafe(48)
    value['access_token'] = access
    db.add(AgentOAuthCredential(token_hash=_hash(access), grant_id=row.id, kind='access', expires_at=utcnow() + timedelta(seconds=ACCESS_SECONDS)))
    if 'offline_access' in value['scope'].split():
        refresh = 'lx_agent_r_' + secrets.token_urlsafe(48)
        value['refresh_token'] = refresh
        db.add(AgentOAuthCredential(token_hash=_hash(refresh), grant_id=row.id, kind='refresh', expires_at=min(_aware(row.expires_at), utcnow() + timedelta(seconds=REFRESH_SECONDS))))
    db.commit()
    return JSONResponse(value, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})


@router.post('/api/agent/oauth/token')
async def token(request: Request, db: Session = Depends(get_db)):
    _reject_browser_origin(request)
    _limit(request, token_limiter)
    data = await _body(request, form=True)
    if data.get('resource') != public_resource():
        _oauth_error('invalid_target', 'token 受众必须为楼响 MCP 资源')
    cid = data.get('client_id', '')
    # Client metadata was validated at authorization; public token exchange has no secret.
    if not cid or len(cid) > 200 or not db.get(AgentOAuthClient, cid):
        _oauth_error('invalid_client', '客户端无效')
    if data.get('client_secret') or data.get('client_assertion') or request.headers.get('authorization'):
        _oauth_error('invalid_client', '此端点仅支持 PKCE 公共客户端')
    if data.get('grant_type') == 'authorization_code':
        code = data.get('code', '')
        verifier = data.get('code_verifier', '')
        if not 32 <= len(code) <= 128 or not VERIFIER_PATTERN.fullmatch(verifier):
            _oauth_error('invalid_grant', '授权码或 PKCE 无效')
        row = db.scalar(select(AgentOAuthCode).where(AgentOAuthCode.code_hash == _hash(code)).with_for_update())
        computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        if (not row or row.used_at or _aware(row.expires_at) <= utcnow() or row.client_id != cid
                or row.redirect_uri != data.get('redirect_uri') or row.resource != data['resource']
                or not secrets.compare_digest(row.challenge, computed)):
            _oauth_error('invalid_grant', '授权码已失效或绑定信息不一致')
        grant = db.get(AgentOAuthGrant, row.grant_id, populate_existing=True)
        try:
            _live_identity(db, grant)
        except HTTPException:
            _oauth_error('invalid_grant', '账号或连接权限已失效')
        consumed = db.execute(update(AgentOAuthCode).where(AgentOAuthCode.id == row.id, AgentOAuthCode.used_at.is_(None)).values(used_at=utcnow()))
        if consumed.rowcount != 1:
            _oauth_error('invalid_grant', '授权码已使用')
        return _issue_tokens(db, grant)
    if data.get('grant_type') == 'refresh_token':
        raw = data.get('refresh_token', '')
        if not TOKEN_PATTERN.fullmatch(raw) or not raw.startswith('lx_agent_r_'):
            _oauth_error('invalid_grant', '刷新凭证无效')
        row = db.scalar(select(AgentOAuthCredential).where(AgentOAuthCredential.token_hash == _hash(raw), AgentOAuthCredential.kind == 'refresh').with_for_update())
        grant = db.get(AgentOAuthGrant, row.grant_id, populate_existing=True) if row else None
        if not row or not grant or grant.client_id != cid or grant.resource != data['resource']:
            _oauth_error('invalid_grant', '刷新凭证无效')
        if row.consumed_at:
            grant.revoked_at = utcnow()
            db.commit()
            _oauth_error('invalid_grant', '刷新凭证已使用，连接已撤销，请重新连接')
        if _aware(row.expires_at) <= utcnow():
            _oauth_error('invalid_grant', '刷新凭证已过期')
        if data.get('scope') and frozenset(data['scope'].split()) != frozenset(grant.scope.split()):
            _oauth_error('invalid_scope', '刷新不能改变原有授权范围，请重新连接')
        try:
            _live_identity(db, grant)
        except HTTPException:
            _oauth_error('invalid_grant', '账号或连接权限已失效')
        consumed = db.execute(update(AgentOAuthCredential).where(AgentOAuthCredential.id == row.id, AgentOAuthCredential.consumed_at.is_(None)).values(consumed_at=utcnow()))
        if consumed.rowcount != 1:
            grant.revoked_at = utcnow()
            db.commit()
            _oauth_error('invalid_grant', '刷新凭证已使用，连接已撤销')
        return _issue_tokens(db, grant)
    _oauth_error('unsupported_grant_type', '不支持此授权模式')


@router.post('/api/agent/oauth/revoke')
async def revoke(request: Request, db: Session = Depends(get_db)):
    _reject_browser_origin(request)
    data = await _body(request, form=True)
    raw = data.get('token', '')
    if TOKEN_PATTERN.fullmatch(raw):
        credential = db.scalar(select(AgentOAuthCredential).where(AgentOAuthCredential.token_hash == _hash(raw)))
        if credential:
            row = db.get(AgentOAuthGrant, credential.grant_id, populate_existing=True)
            if row and row.client_id == data.get('client_id'):
                row.revoked_at = utcnow()
                db.commit()
    return JSONResponse({}, headers={'Cache-Control': 'no-store'})


@router.get('/api/agent/connections')
def connections(ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    rows = db.scalars(select(AgentOAuthGrant).where(AgentOAuthGrant.organization_id == ctx.tenant_id,
        AgentOAuthGrant.user_id == ctx.user_id, AgentOAuthGrant.revoked_at.is_(None)).order_by(AgentOAuthGrant.created_at.desc())).all()
    result = []
    for row in rows:
        if _aware(row.expires_at) <= utcnow():
            continue
        client = db.get(AgentOAuthClient, row.client_id)
        live = _live_identity(db, row)
        result.append({'id': row.id, 'client_id': row.client_id, 'client_name': client.name if client else '外部 Agent',
            'project_ids': list(live.project_ids), 'scopes': sorted(live.scopes), 'created_at': row.created_at.isoformat(), 'expires_at': row.expires_at.isoformat()})
    return result


@router.delete('/api/agent/connections/{grant_id}')
def disconnect(grant_id: str, request: Request, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    _origin_required(request)
    row = db.scalar(select(AgentOAuthGrant).where(AgentOAuthGrant.id == grant_id,
        AgentOAuthGrant.organization_id == ctx.tenant_id, AgentOAuthGrant.user_id == ctx.user_id))
    if not row:
        raise HTTPException(404, '连接不存在')
    row.revoked_at = utcnow()
    db.commit()
    return {'status': 'revoked'}


@router.get('/api/agent/whoami')
def whoami(request: Request, db: Session = Depends(get_db)):
    identity = authenticate_agent(db, request.headers.get('authorization', ''))
    grant = db.get(AgentOAuthGrant, identity.grant_id)
    organization = db.get(Tenant, identity.ctx.tenant_id)
    request.state.context = identity.ctx
    return {'user': {'id': identity.ctx.user_id, 'name': identity.ctx.name},
        'organization': {'id': identity.ctx.tenant_id, 'name': organization.name},
        'project_ids': list(identity.project_ids), 'scopes': sorted(identity.scopes),
        'grant_id': identity.grant_id, 'client_id': identity.client_id,
        'expires_at': grant.expires_at.isoformat()}
