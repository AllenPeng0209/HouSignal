"""OAuth identity records; intentionally separate from tenant-scoped business RLS.

Only hashed bearer credentials are persisted. The resource server re-establishes
current account tenancy before touching any business table.
"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, new_id, utcnow


class AgentOAuthClient(Base):
    __tablename__ = 'external_agent_clients'
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    redirect_uris: Mapped[list] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(20), default='registration')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentOAuthAuthorization(Base):
    __tablename__ = 'external_agent_authorizations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[str] = mapped_column(ForeignKey('external_agent_clients.id'))
    redirect_uri: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(String(512))
    scope: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(Text)
    challenge: Mapped[str] = mapped_column(String(43))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentOAuthGrant(Base):
    __tablename__ = 'external_agent_grants'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey('external_agent_clients.id'), index=True)
    resource: Mapped[str] = mapped_column(String(512))
    scope: Mapped[str] = mapped_column(String(200))
    project_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentOAuthCode(Base):
    __tablename__ = 'external_agent_codes'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    grant_id: Mapped[str] = mapped_column(ForeignKey('external_agent_grants.id'), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey('external_agent_clients.id'))
    redirect_uri: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(String(512))
    challenge: Mapped[str] = mapped_column(String(43))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentOAuthCredential(Base):
    __tablename__ = 'external_agent_credentials'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    grant_id: Mapped[str] = mapped_column(ForeignKey('external_agent_grants.id'), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
