from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from .config import settings


def new_id():
    return str(uuid4())


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True, **({'connect_args': {'check_same_thread':False}} if settings.database_url.startswith('sqlite') else {}))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(Session, 'after_begin')
def restore_tenant_context(session, transaction, connection):
    if connection.dialect.name == 'postgresql' and session.info.get('tenant_id'):
        connection.execute(text("SELECT set_config('app.tenant_id', :tenant, true), set_config('app.user_id', :user, true)"),
                           {'tenant':session.info['tenant_id'],'user':session.info.get('user_id','')})


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
