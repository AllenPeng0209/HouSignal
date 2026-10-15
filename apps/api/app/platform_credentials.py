"""Encrypted, project-owned WeChat official API configuration.

API token validation is independent of browser authentication and does not grant
publication authority. Only bounded public metadata leaves this module; tokens
are discarded after verification, and raw supplier errors are never returned.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import html
import json
import os
from pathlib import Path
import re
import tempfile

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.orm import Session

from .db import get_db
from .platform_runtime import runtime
from .security import Context, decrypt_pii, encrypt_pii, get_context, require_project

ROLES = ('owner', 'admin', 'content')
_ENDPOINT = 'https://api.weixin.qq.com/cgi-bin/stable_token'
_MESSAGES = {40164: '请在公众号后台核对服务器出口 IP 白名单。',
             40125: 'AppSecret 未通过验证，请在公众号后台核对。',
             40013: 'AppID 未通过验证，请核对公众号开发者信息。'}


class SafeCredentialRoute(APIRoute):
    """Protect secrets even if this router is assembled without app handlers."""
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                if request.method == 'POST' and len(await request.body()) > 8192:
                    raise HTTPException(413, '公众号凭证请求过大')
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, '公众号凭证参数格式无效') from None
        return safe


router = APIRouter(prefix='/{platform}/credentials', tags=['公众号 API 凭证'], route_class=SafeCredentialRoute)


class CredentialsBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    appId: SecretStr
    appSecret: SecretStr
    name: str = ''
    author: str = ''


def _platform(platform):
    if platform != 'wechat-oa':
        raise HTTPException(404, '该平台不使用公众号 API 凭证')


def _text(value, limit=80):
    value = html.unescape(re.sub(r'<[^>]*>', '', str(value or '')))
    return re.sub(r'[\x00-\x1f\x7f]', ' ', value).strip()[:limit]


def _directory(tenant, pid, *, create=False):
    path = runtime._scope(tenant, pid, 'wechat-oa', create=create) / 'credentials'
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise OSError('invalid credential directory')
    if create:
        path.mkdir(exist_ok=True, mode=0o700)
        path.chmod(0o700)
    return path


@contextmanager
def _guard(path):
    fd = os.open(path / '.credentials.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read(path):
    if path.is_symlink():
        raise OSError('invalid credential file')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    with os.fdopen(fd) as file:
        raw = file.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        raise OSError('invalid credential file')
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _write(path, value):
    if path.is_symlink():
        raise OSError('invalid credential file')
    fd, temporary = tempfile.mkstemp(prefix='.credentials-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _timestamp(value):
    try:
        date = datetime.fromisoformat(value)
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _metadata(saved):
    app_id = ''
    try:
        app_id = decrypt_pii(saved.get('appId_cipher', ''))
    except Exception:
        pass  # Invalid encryption state must never be reflected to the caller.
    configured = bool(app_id and isinstance(saved.get('appSecret_cipher'), str) and saved['appSecret_cipher'])
    expiry = _timestamp(saved.get('verification_expires_at'))
    verified = bool(configured and saved.get('verified') is True and expiry and expiry > datetime.now(timezone.utc))
    code = saved.get('code')
    if not isinstance(code, int) or isinstance(code, bool) or not -1 <= code <= 999999:
        code = None
    message = ('官方 API 凭证已核对；具体接口权限尚未检查。' if verified else
               (_MESSAGES.get(code, '凭证已保存，官方 API 验证未通过或已过期。') if configured else '尚未配置公众号 API 凭证。'))
    verified_at = _timestamp(saved.get('verified_at'))
    return {'configured': configured, 'verified': verified, 'mode': 'official_api',
            'appIdMasked': app_id[:6] + '***' + app_id[-4:] if len(app_id) > 10 else ('***' if app_id else ''),
            'name': _text(saved.get('name')), 'author': _text(saved.get('author')),
            'verified_at': verified_at.isoformat() if verified_at else None,
            'verification_expires_at': expiry.isoformat() if expiry else None,
            'code': code, 'message': message, 'capabilities': 'not_checked'}


def credentials_metadata(tenant, pid):
    """Allow account/agent metadata aggregation without reading a secret."""
    try:
        return _metadata(_read(_directory(tenant, pid) / 'config.json'))
    except (OSError, ValueError):
        raise HTTPException(409, '公众号凭证存储暂不可用') from None


def _http_client():
    return httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False, trust_env=False)


def _verify(app_id, secret):
    try:
        with _http_client() as client:
            with client.stream('POST', _ENDPOINT, json={'grant_type': 'client_credential',
                    'appid': app_id, 'secret': secret, 'force_refresh': False}) as response:
                if response.status_code != 200:
                    return False, None, None
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 32 * 1024:
                        return False, None, None
        value = json.loads(raw)
        if not isinstance(value, dict):
            return False, None, None
        code = value.get('errcode')
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return False, code if -1 <= code <= 999999 else None, None
        token, expiry = value.get('access_token'), value.get('expires_in')
        if isinstance(token, str) and 1 <= len(token) <= 8192 and isinstance(expiry, int) and not isinstance(expiry, bool) and 0 < expiry <= 7200:
            return True, 0, expiry
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        pass
    return False, None, None


@router.get('')
def get_credentials(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid)
    _platform(platform)
    return credentials_metadata(ctx.tenant_id, pid)


@router.post('')
def save_credentials(pid: str, platform: str, body: CredentialsBody, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    _platform(platform)
    app_id, secret = body.appId.get_secret_value().strip(), body.appSecret.get_secret_value().strip()
    if not re.fullmatch(r'wx[A-Za-z0-9]{16}', app_id) or not re.fullmatch(r'[A-Za-z0-9]{32}', secret) or len(body.name) > 200 or len(body.author) > 200:
        raise HTTPException(422, '请核对公众号 AppID、AppSecret 与账号资料')
    try:
        directory = _directory(ctx.tenant_id, pid, create=True)
        with _guard(directory):
            # Check the destination before issuing a supplier request.
            _read(directory / 'config.json')
            verified, code, expiry = _verify(app_id, secret)
            now = datetime.now(timezone.utc)
            saved = {'appId_cipher': encrypt_pii(app_id), 'appSecret_cipher': encrypt_pii(secret),
                     'name': _text(body.name), 'author': _text(body.author), 'verified': verified,
                     'code': code, 'verified_at': now.isoformat() if verified else None,
                     'verification_expires_at': (now + timedelta(seconds=expiry)).isoformat() if expiry else None}
            _write(directory / 'config.json', saved)
            return _metadata(saved)
    except (OSError, ValueError):
        raise HTTPException(409, '公众号凭证正在操作或存储暂不可用') from None



@router.delete('')
def clear_credentials(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    _platform(platform)
    try:
        directory = _directory(ctx.tenant_id, pid)
        if directory.exists():
            with _guard(directory):
                path = directory / 'config.json'
                if path.is_symlink():
                    raise OSError('invalid credential file')
                path.unlink(missing_ok=True)
        return _metadata({})
    except (OSError, ValueError):
        raise HTTPException(409, '公众号凭证正在操作或存储暂不可用') from None
