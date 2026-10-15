"""Project-scoped, durable conversations, tool provenance and token accounting."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, new_id, utcnow


class ProjectRecord:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(ProjectRecord, Base):
    __tablename__ = "conversations"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), default="新对话")
    draft: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)


class ChatMessage(ProjectRecord, Base):
    __tablename__ = "chat_messages"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    __table_args__ = (Index("ix_chat_conversation_time", "conversation_id", "created_at"),)


class ToolCall(ProjectRecord, Base):
    __tablename__ = "tool_calls"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    result_refs: Mapped[list] = mapped_column(JSON, default=list)


class ModelUsage(ProjectRecord, Base):
    __tablename__ = "model_usage"
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)


class AgentControl(ProjectRecord, Base):
    """Durable, actor-bound Pi steering and follow-up input."""
    __tablename__ = "agent_controls"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    private_input_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
