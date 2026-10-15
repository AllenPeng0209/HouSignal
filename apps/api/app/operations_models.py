"""Project-scoped account briefs, sourced discoveries, and editorial plans."""
from datetime import date
from sqlalchemy import Date, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .business_models import ProjectEntity


class ContentAccount(ProjectEntity, Base):
    __tablename__ = 'content_accounts'
    platform: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    homepage: Mapped[str] = mapped_column(Text, default='')
    positioning: Mapped[str] = mapped_column(Text, default='')
    audience: Mapped[str] = mapped_column(Text, default='')
    voice: Mapped[str] = mapped_column(Text, default='')
    red_lines: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(30), default='managed')


class ContentOpportunity(ProjectEntity, Base):
    __tablename__ = 'content_opportunities'
    __table_args__ = (UniqueConstraint('tenant_id', 'property_id', 'source_key', name='uq_content_opportunity_source'),)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(30))
    platform: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str] = mapped_column(Text, default='')
    summary: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(30), default='saved')
    source_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    curation_state: Mapped[str] = mapped_column(String(30), default='inbox', server_default='inbox')
    tags: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    note: Mapped[str] = mapped_column(Text, default='', server_default='')
    media_asset_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('assets.id', name='fk_opportunity_media_asset'), nullable=True)
    research_notes: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    analysis_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')


class DiscoverySavedSearch(ProjectEntity, Base):
    __tablename__ = 'discovery_saved_searches'
    name: Mapped[str] = mapped_column(String(200))
    filters: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')


class ContentPlan(ProjectEntity, Base):
    __tablename__ = 'content_plans'
    __table_args__ = (UniqueConstraint('tenant_id', 'property_id', 'opportunity_id', name='uq_content_plan_opportunity'),)
    title: Mapped[str] = mapped_column(String(300))
    angle: Mapped[str] = mapped_column(Text, default='')
    channel: Mapped[str] = mapped_column(String(40))
    scheduled_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('content_accounts.id'), nullable=True, index=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('content_opportunities.id'), nullable=True, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('content_opportunities.id', name='fk_content_plan_reference'), nullable=True, index=True)
    topic_id: Mapped[str] = mapped_column(String(30))
    content_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('content_versions.id'), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default='draft')
    brief: Mapped[str] = mapped_column(Text, default='')
    source_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

