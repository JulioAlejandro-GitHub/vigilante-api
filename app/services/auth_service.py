from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppUser, Role, UserOrganizationScope, UserRole


PASSWORD_SCHEME = "pbkdf2_sha256"


class AuthError(Exception):
    pass


class InvalidCredentialsError(AuthError):
    pass


class InactiveUserError(AuthError):
    pass


def hash_password(password: str, *, iterations: int | None = None, salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("password is required")
    iteration_count = iterations or get_settings().auth_password_pbkdf2_iterations
    password_salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), password_salt, iteration_count)
    return "$".join(
        [
            PASSWORD_SCHEME,
            str(iteration_count),
            base64.urlsafe_b64encode(password_salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        ]
    )


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        scheme, iterations_raw, salt_raw, digest_raw = password_hash.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_raw)
        salt = _decode_b64(salt_raw)
        expected_digest = _decode_b64(digest_raw)
    except (ValueError, TypeError):
        return False
    actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual_digest, expected_digest)


def authenticate_user(session: Session, *, username: str, password: str) -> AppUser:
    user = find_user_by_login(session, username)
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("Invalid username or password")
    ensure_user_active(user)
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return user


def ensure_user_active(user: AppUser) -> None:
    if not user.is_active or user.status != "active":
        raise InactiveUserError("User is inactive")


def find_user_by_login(session: Session, username: str) -> AppUser | None:
    normalized = username.strip().lower()
    if not normalized:
        return None
    parsed_id = _parse_uuid(normalized)
    if parsed_id is not None:
        direct = session.get(AppUser, parsed_id)
        if direct is not None:
            return direct

    email_match = session.scalar(select(AppUser).where(AppUser.email == username.strip()))
    if email_match is not None:
        return email_match

    for user in session.scalars(select(AppUser)).all():
        if user.email.lower() == normalized:
            return user
        metadata_username = user_username(user).lower()
        if metadata_username == normalized:
            return user
    return None


def get_user_roles(session: Session, user_id: UUID) -> list[str]:
    rows = session.scalars(
        select(Role)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.role_key.asc())
    ).all()
    return [str(row.role_key).lower() for row in rows]


def get_user_organization_scopes(session: Session, user_id: UUID) -> list[UserOrganizationScope]:
    return list(
        session.scalars(
            select(UserOrganizationScope)
            .where(UserOrganizationScope.user_id == user_id)
            .order_by(UserOrganizationScope.organization_id.asc())
        ).all()
    )


def user_username(user: AppUser) -> str:
    metadata = dict(user.user_metadata or {})
    username = metadata.get("username")
    if username:
        return str(username)
    return user.email.split("@", 1)[0] if "@" in user.email else user.email


def _decode_b64(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + ("=" * (-len(value) % 4))).encode("ascii"))


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
