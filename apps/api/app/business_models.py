"""Versioned content, release evidence, and consent-aware sales records."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def uuid(): return str(uuid4())
def now(): return datetime.now(timezone.utc)


class ProjectEntity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey('tenants.id'), index=True)
    property_id: Mapped[str] = mapped_column(String(36), ForeignKey('properties.id'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ContentVersion(ProjectEntity, Base):
    __tablename__='content_versions'
    __table_args__=(UniqueConstraint('tenant_id','property_id','series_id','version',name='uq_content_series_version'),)
    series_id: Mapped[str] = mapped_column(String(36), default=uuid,index=True)
    version: Mapped[int] = mapped_column(Integer,default=1)
    topic: Mapped[str] = mapped_column(Text,default='')
    script: Mapped[str] = mapped_column(Text,default='')
    fact_refs: Mapped[list] = mapped_column(JSON,default=list)
    storyboard: Mapped[list] = mapped_column(JSON,default=list)
    channel: Mapped[str] = mapped_column(String(40),default='抖音')
    cover_title: Mapped[str] = mapped_column(String(120),default='')
    aspect_ratio: Mapped[str] = mapped_column(String(12),default='9:16')
    subtitle_style: Mapped[str] = mapped_column(String(40),default='minimal')
    render_asset_id: Mapped[str | None] = mapped_column(String(36),nullable=True)
    status: Mapped[str] = mapped_column(String(30),default='draft')
    created_by: Mapped[str] = mapped_column(String(36),ForeignKey('users.id'))


class Release(ProjectEntity, Base):
    __tablename__='releases'
    content_version_id: Mapped[str] = mapped_column(String(36),ForeignKey('content_versions.id'),index=True)
    status: Mapped[str] = mapped_column(String(30),default='approved')
    snapshot: Mapped[dict] = mapped_column(JSON,default=dict)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[str] = mapped_column(String(36),ForeignKey('users.id'))
    review_note: Mapped[str] = mapped_column(Text,default='')


class Publication(ProjectEntity, Base):
    __tablename__='publications'
    release_id: Mapped[str] = mapped_column(String(36),ForeignKey('releases.id'),index=True)
    title: Mapped[str] = mapped_column(Text,default='')
    channel: Mapped[str] = mapped_column(String(40),default='抖音')
    url: Mapped[str] = mapped_column(Text)
    platform_state: Mapped[str] = mapped_column(String(30),default='pending')
    receipt_status: Mapped[str] = mapped_column(String(30),default='unverified')
    metrics: Mapped[dict] = mapped_column(JSON,default=lambda:{'D7':None,'D30':None})
    # Recording a receipt is not evidence of when the work actually went live.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True)


class Inquiry(ProjectEntity, Base):
    __tablename__='inquiries'
    __table_args__=(UniqueConstraint('tenant_id','property_id','request_key',name='uq_project_inquiry_request'),)
    name: Mapped[str] = mapped_column(String(120),default='新咨询')
    need: Mapped[str] = mapped_column(Text,default='')
    channel: Mapped[str] = mapped_column(String(40),default='人工录入')
    source_confidence: Mapped[str] = mapped_column(String(30),default='unknown')
    source_text: Mapped[str] = mapped_column(Text,default='')
    publication_id: Mapped[str | None] = mapped_column(String(36),ForeignKey('publications.id',name='fk_inquiry_publication'),nullable=True,index=True)
    contact_cipher: Mapped[str | None] = mapped_column(Text,nullable=True)
    contact_fingerprint: Mapped[str | None] = mapped_column(String(64),nullable=True,index=True)
    request_key: Mapped[str | None] = mapped_column(String(120),nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64),nullable=True)
    linked_lead_id: Mapped[str | None] = mapped_column(String(36),ForeignKey('leads.id',name='fk_inquiry_linked_lead',use_alter=True),nullable=True,index=True)
    contactable: Mapped[bool] = mapped_column(Boolean,default=False)
    consent: Mapped[bool] = mapped_column(Boolean,default=False)
    qualified: Mapped[bool] = mapped_column(Boolean,default=False)
    owner_id: Mapped[str | None] = mapped_column(String(36),ForeignKey('users.id'),nullable=True)
    qualification_checks: Mapped[dict] = mapped_column(JSON,default=dict)
    withdrawn: Mapped[bool] = mapped_column(Boolean,default=False)


class Lead(ProjectEntity, Base):
    __tablename__='leads'
    __table_args__=(UniqueConstraint('inquiry_id',name='uq_lead_inquiry'),)
    inquiry_id: Mapped[str] = mapped_column(String(36),ForeignKey('inquiries.id'),index=True)
    name: Mapped[str] = mapped_column(String(120))
    need: Mapped[str] = mapped_column(Text,default='')
    stage: Mapped[str] = mapped_column(String(30),default='qualified')
    owner_id: Mapped[str] = mapped_column(String(36),ForeignKey('users.id'))
    next_action: Mapped[str] = mapped_column(Text,default='确认沟通时间')
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True)
    source_confidence: Mapped[str] = mapped_column(String(30),default='unknown')
    stage_history: Mapped[list] = mapped_column(JSON,default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now,onupdate=now)


class FollowUp(ProjectEntity, Base):
    __tablename__='followups'
    lead_id: Mapped[str] = mapped_column(String(36),ForeignKey('leads.id'),index=True)
    note: Mapped[str] = mapped_column(Text)
    next_action: Mapped[str] = mapped_column(Text,default='')
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True)
    created_by: Mapped[str] = mapped_column(String(36),ForeignKey('users.id'))


class Proposal(ProjectEntity, Base):
    __tablename__='proposals'
    title: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text,default='')
    status: Mapped[str] = mapped_column(String(30),default='pending')
    source_refs: Mapped[list] = mapped_column(JSON,default=list)


class ContentReviewFeedback(ProjectEntity, Base):
    __tablename__='content_review_feedback'
    content_version_id: Mapped[str] = mapped_column(String(36),ForeignKey('content_versions.id'),index=True)
    version: Mapped[int] = mapped_column(Integer)
    render_asset_id: Mapped[str | None] = mapped_column(String(36),nullable=True)
    reviewer_id: Mapped[str] = mapped_column(String(36),ForeignKey('users.id'))
    reviewer_name: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text)
    shot_id: Mapped[str | None] = mapped_column(String(100),nullable=True)
    sentence: Mapped[str | None] = mapped_column(Text,nullable=True)


# Register native CRM detail models with every shared model bootstrap.
from . import secretary_models  # noqa: E402,F401
