from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db import Base


API_SCHEMA = get_settings().api_schema
API_TABLE_ARGS = {"schema": API_SCHEMA} if API_SCHEMA else {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Organization(Base):
    __tablename__ = "organization"
    __table_args__ = API_TABLE_ARGS

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)


class Site(Base):
    __tablename__ = "site"
    __table_args__ = API_TABLE_ARGS

    site_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class Camera(Base):
    __tablename__ = "camera"
    __table_args__ = API_TABLE_ARGS

    camera_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    external_camera_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class PersonProfile(Base):
    __tablename__ = "person_profile"
    __table_args__ = API_TABLE_ARGS

    person_profile_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class TimelineEvent(Base):
    __tablename__ = "timeline_event"
    __table_args__ = (
        UniqueConstraint("source_component", "source_event_id", name="uq_timeline_event_source_component_event"),
        API_TABLE_ARGS,
    ) if API_SCHEMA else (
        UniqueConstraint("source_component", "source_event_id", name="uq_timeline_event_source_component_event"),
    )

    timeline_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    source_component: Mapped[str] = mapped_column(String, nullable=False)
    source_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    site_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    zone_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    camera_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    observed_subject_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    person_profile_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    case_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    organization_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
