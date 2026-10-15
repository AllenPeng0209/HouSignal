"""CRM details and durable, local viewing appointments for the native secretary."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, utcnow
from .business_models import ProjectEntity


class CustomerProfile(ProjectEntity, Base):
    __tablename__='customer_profiles'
    __table_args__=(UniqueConstraint('lead_id',name='uq_customer_profile_lead'),)
    lead_id: Mapped[str] = mapped_column(String(36),ForeignKey('leads.id'),index=True)
    details: Mapped[dict] = mapped_column(JSON,default=dict)
    provenance: Mapped[dict] = mapped_column(JSON,default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow,onupdate=utcnow)


class ViewingAppointment(ProjectEntity, Base):
    __tablename__='viewing_appointments'
    lead_id: Mapped[str] = mapped_column(String(36),ForeignKey('leads.id'),index=True)
    date: Mapped[str] = mapped_column(String(10),default='')
    time_window: Mapped[str] = mapped_column(String(80),default='')
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True)
    timezone: Mapped[str] = mapped_column(String(60),default='Asia/Shanghai')
    location: Mapped[str] = mapped_column(Text,default='')
    notes: Mapped[str] = mapped_column(Text,default='')
    status: Mapped[str] = mapped_column(String(30),default='pending_details')
    source_note: Mapped[str] = mapped_column(Text,default='')
    created_by: Mapped[str] = mapped_column(String(36),ForeignKey('users.id'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow,onupdate=utcnow)
