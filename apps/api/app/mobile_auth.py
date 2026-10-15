"""Revocable opaque native credentials backed by the existing login sessions."""
from datetime import timedelta
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core import LoginInput, _auth_dict
from .core_models import LoginSession, Membership, User
from .db import get_db, utcnow
from .security import MOBILE_TOKEN_PREFIX, Context, context_for_user, get_context, mobile_token_from_request, verify_password


router = APIRouter(prefix='/api/mobile/auth', tags=['mobile-auth'])


@router.post('/login')
def login(data: LoginInput, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.strip().lower(), User.active.is_(True)))
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, '账号或密码不正确')
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.active.is_(True)).order_by(Membership.id))
    if not membership:
        raise HTTPException(403, '账号没有可用组织')
    ctx = context_for_user(db, user.id, membership.tenant_id)
    token = MOBILE_TOKEN_PREFIX + secrets.token_urlsafe(48)
    expires_at = utcnow() + timedelta(days=7)
    db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                        user_id=user.id, tenant_id=membership.tenant_id, expires_at=expires_at))
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return {'access_token': token, 'token_type': 'bearer', 'expires_at': expires_at.isoformat(), **_auth_dict(db, ctx)}


@router.post('/logout')
def logout(request: Request, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    # A browser cookie must never revoke a separate native session.
    token = mobile_token_from_request(request)
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if session:
        db.delete(session)
        db.commit()
    return {'ok': True}
