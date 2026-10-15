"""Explicit owner provisioning for a clean deployment; no demo projects."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.config import settings
from app.core_models import User, Tenant, Membership
from app.security import hash_password

if len(settings.bootstrap_password) < 12 or settings.bootstrap_password == 'Louxiang2026!':
    raise SystemExit('Set an independent BOOTSTRAP_PASSWORD of at least 12 characters')
with Session(create_engine(settings.migration_database_url, hide_parameters=True)) as db:
    if db.scalar(select(User).where(User.email == settings.bootstrap_email)):
        raise SystemExit('Account already exists; no changes made')
    tenant = Tenant(name='楼响工作空间')
    user = User(email=settings.bootstrap_email, name='组织管理员', password_hash=hash_password(settings.bootstrap_password))
    db.add_all([tenant, user]); db.flush()
    db.add(Membership(user_id=user.id, tenant_id=tenant.id, role='admin'))
    db.commit()
print('Organization owner created; no fictional project data loaded')
