from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import re
import secrets
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .core_models import LoginSession, Membership, Property, User


MOBILE_TOKEN_PREFIX = 'hs_mobile_'
MOBILE_TOKEN_PATTERN = re.compile(r'hs_mobile_[A-Za-z0-9_-]{64}')


@dataclass(frozen=True)
class Context:
    user_id: str
    tenant_id: str
    role: str
    name: str
    email: str
    project_ids: list[str] | None = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$' + base64.urlsafe_b64encode(salt).decode() + '$' + base64.urlsafe_b64encode(derived).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, digest = encoded.split('$')
        if algorithm != 'scrypt':
            return False
        derived = hashlib.scrypt(password.encode(), salt=base64.urlsafe_b64decode(salt), n=16384, r=8, p=1, dklen=32)
        return secrets.compare_digest(derived, base64.urlsafe_b64decode(digest))
    except (ValueError, TypeError):
        return False


def _fernet():
    key = settings.pii_encryption_key or base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt_pii(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ''


def decrypt_pii(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode() if value else ''


def pii_fingerprint(value: str, tenant_id: str) -> str:
    normalized = ''.join(value.split()).replace('-', '')
    key = hmac.new(settings.secret_key.encode(), tenant_id.encode(), hashlib.sha256).digest()
    return hmac.new(key, normalized.encode(), hashlib.sha256).hexdigest() if normalized else ''


def context_for_user(db: Session, user_id: str, tenant_id: str) -> Context:
    user = db.get(User, user_id)
    membership = db.scalar(select(Membership).where(Membership.user_id == user_id, Membership.tenant_id == tenant_id, Membership.active.is_(True)))
    if not user or not user.active or not membership:
        raise HTTPException(403, '账号或组织权限已失效')
    ctx = Context(user.id, tenant_id, membership.role, user.name, user.email, membership.project_ids)
    db.info['tenant_id'] = tenant_id
    db.info['user_id'] = user_id
    if db.bind.dialect.name == 'postgresql':
        db.execute(text("SELECT set_config('app.tenant_id', :tenant, true), set_config('app.user_id', :user, true)"), {'tenant':tenant_id,'user':user_id})
    return ctx


def mobile_token_from_request(request: Request) -> str:
    authorization = request.headers.get('authorization', '')
    scheme, separator, token = authorization.partition(' ')
    if not separator or scheme.lower() != 'bearer' or not MOBILE_TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(401, '手机登录凭证无效，请重新登录')
    return token


def get_context(request: Request, db: Session = Depends(get_db)) -> Context:
    if 'authorization' in request.headers:
        token = mobile_token_from_request(request)
        request.state.auth_method = 'bearer'
    else:
        token = request.cookies.get('hs_session')
        if token and token.startswith(MOBILE_TOKEN_PREFIX):
            raise HTTPException(401, '登录凭证类型无效，请重新登录')
        request.state.auth_method = 'cookie'
    if not token:
        raise HTTPException(401, '请先登录')
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if not session:
        raise HTTPException(401, '登录已过期，请重新登录')
    expiry = session.expires_at.replace(tzinfo=timezone.utc) if session.expires_at.tzinfo is None else session.expires_at
    if expiry <= datetime.now(timezone.utc):
        raise HTTPException(401, '登录已过期，请重新登录')
    ctx = context_for_user(db, session.user_id, session.tenant_id)
    request.state.context = ctx
    return ctx


def require_project(db: Session, ctx: Context, project_id: str, roles: list[str] | tuple[str,...] | None = None) -> Property:
    property = db.scalar(select(Property).where(Property.id == project_id, Property.tenant_id == ctx.tenant_id))
    if not property or (ctx.project_ids is not None and project_id not in ctx.project_ids):
        raise HTTPException(404, '项目不存在或无权访问')
    if roles is not None and ctx.role not in roles:
        raise HTTPException(403, '当前角色没有操作权限')
    return property
