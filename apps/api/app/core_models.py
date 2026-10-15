from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, new_id, utcnow
from datetime import datetime


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Tenant(Base):
    __tablename__ = 'tenants'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))


class Membership(Base):
    __tablename__ = 'memberships'
    __table_args__ = (UniqueConstraint('user_id','tenant_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    role: Mapped[str] = mapped_column(String(30), default='viewer')
    project_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Property(Base):
    __tablename__ = 'properties'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100))
    property_type: Mapped[str] = mapped_column(String(100), default='豪宅住宅')
    owner_name: Mapped[str] = mapped_column(String(100), default='')
    audience: Mapped[str] = mapped_column(Text, default='')
    sales_owner_name: Mapped[str] = mapped_column(String(100), default='')
    channel: Mapped[str] = mapped_column(String(100), default='抖音')
    marketing_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    marketing_evidence: Mapped[str] = mapped_column(Text, default='')
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Fact(Base):
    __tablename__ = 'facts'
    __table_args__ = (UniqueConstraint('property_id','semantic_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    property_id: Mapped[str] = mapped_column(ForeignKey('properties.id'), index=True)
    semantic_key: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(Text, default='')
    unit: Mapped[str] = mapped_column(String(50), default='')
    source: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(30), default='unverified')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FactRevision(Base):
    __tablename__ = 'fact_revisions'
    __table_args__ = (UniqueConstraint('fact_id','revision'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    property_id: Mapped[str] = mapped_column(ForeignKey('properties.id'), index=True)
    fact_id: Mapped[str] = mapped_column(ForeignKey('facts.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Asset(Base):
    __tablename__ = 'assets'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    property_id: Mapped[str] = mapped_column(ForeignKey('properties.id'), index=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(30))
    storage_key: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    rights: Mapped[str] = mapped_column(String(30), default='pending')
    rights_evidence: Mapped[str] = mapped_column(Text, default='')
    rights_revision: Mapped[int] = mapped_column(Integer, default=1)
    origin_type: Mapped[str] = mapped_column(String(30), default='actual')
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    extracted_text: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
