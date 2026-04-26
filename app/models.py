from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, JSON, SmallInteger, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db import Base


API_SCHEMA = get_settings().api_schema
API_TABLE_ARGS = {"schema": API_SCHEMA} if API_SCHEMA else {}
AUTH_SCHEMA = get_settings().auth_schema
AUTH_TABLE_ARGS = {"schema": AUTH_SCHEMA} if AUTH_SCHEMA else {}


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
    organization_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)


class Camera(Base):
    __tablename__ = "camera"
    __table_args__ = API_TABLE_ARGS

    camera_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    external_camera_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    site_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)


class Zone(Base):
    __tablename__ = "zone"
    __table_args__ = API_TABLE_ARGS

    zone_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    site_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)


class PersonProfile(Base):
    __tablename__ = "person_profile"
    __table_args__ = API_TABLE_ARGS

    person_profile_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)


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
    site_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    zone_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    camera_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    observed_subject_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    person_profile_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    case_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    organization_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)


class CaseRecord(Base):
    __tablename__ = "case_record"
    __table_args__ = API_TABLE_ARGS

    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    case_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    case_type: Mapped[str] = mapped_column(Text, nullable=False)
    case_status: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    primary_camera_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    primary_observed_subject_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    primary_person_profile_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    assigned_to_user_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    case_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    organization_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    site_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    zone_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)


class CaseItem(Base):
    __tablename__ = "case_item"
    __table_args__ = API_TABLE_ARGS

    case_item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    item_ref_uuid: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    item_ref_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    added_by_user_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class CaseNote(Base):
    __tablename__ = "case_note"
    __table_args__ = API_TABLE_ARGS

    case_note_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    author_user_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class CaseStatusHistory(Base):
    __tablename__ = "case_status_history"
    __table_args__ = API_TABLE_ARGS

    case_status_history_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    old_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_status: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by_user_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = AUTH_TABLE_ARGS

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    user_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Role(Base):
    __tablename__ = "role"
    __table_args__ = AUTH_TABLE_ARGS

    role_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    role_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class UserRole(Base):
    __tablename__ = "user_role"
    __table_args__ = AUTH_TABLE_ARGS

    user_role_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class UserOrganizationScope(Base):
    __tablename__ = "user_organization_scope"
    __table_args__ = AUTH_TABLE_ARGS

    user_organization_scope_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scope_role: Mapped[str] = mapped_column(Text, nullable=False)
    can_view: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    can_operate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scope_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
