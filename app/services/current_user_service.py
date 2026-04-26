from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_dependency
from app.models import AppUser, Site
from app.services.auth_service import InactiveUserError, ensure_user_active, get_user_organization_scopes, get_user_roles, user_username
from app.services.rbac_service import canonical_role
from app.services.token_service import TokenError, decode_access_token


class CurrentUserScope(BaseModel):
    organization_id: str
    site_ids: list[str] = Field(default_factory=list)
    all_sites: bool = False
    scope_role: str
    can_view: bool
    can_operate: bool
    can_admin: bool


class CurrentUser(BaseModel):
    user_id: str
    username: str
    email: str
    display_name: str
    role: str
    roles: list[str]
    is_active: bool
    organization_ids: list[str] = Field(default_factory=list)
    site_ids: list[str] = Field(default_factory=list)
    scopes: list[CurrentUserScope] = Field(default_factory=list)


def build_current_user(session: Session, user: AppUser) -> CurrentUser:
    roles = get_user_roles(session, user.user_id)
    scopes = [_read_scope(session, row) for row in get_user_organization_scopes(session, user.user_id)]
    organization_ids = sorted({scope.organization_id for scope in scopes})
    site_ids = sorted({site_id for scope in scopes for site_id in scope.site_ids})
    return CurrentUser(
        user_id=str(user.user_id),
        username=user_username(user),
        email=user.email,
        display_name=user.display_name,
        role=canonical_role(roles),
        roles=roles,
        is_active=user.is_active and user.status == "active",
        organization_ids=organization_ids,
        site_ids=site_ids,
        scopes=scopes,
    )


def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    session: Session = Depends(session_dependency),
) -> CurrentUser:
    token = _extract_bearer_token(authorization)
    try:
        payload = decode_access_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    try:
        parsed_user_id = UUID(str(user_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = session.get(AppUser, parsed_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token user no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        ensure_user_active(user)
    except InactiveUserError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive") from exc
    return build_current_user(session, user)


def _read_scope(session: Session, row) -> CurrentUserScope:
    metadata = dict(row.scope_metadata or {})
    metadata_site_ids = _metadata_site_ids(metadata)
    all_sites = metadata_site_ids is None
    site_ids = metadata_site_ids if metadata_site_ids is not None else _site_ids_for_organization(session, row.organization_id)
    return CurrentUserScope(
        organization_id=str(row.organization_id),
        site_ids=site_ids,
        all_sites=all_sites,
        scope_role=str(row.scope_role),
        can_view=bool(row.can_view),
        can_operate=bool(row.can_operate),
        can_admin=bool(row.can_admin),
    )


def _metadata_site_ids(metadata: dict[str, Any]) -> list[str] | None:
    raw = metadata.get("site_ids")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if item]


def _site_ids_for_organization(session: Session, organization_id) -> list[str]:
    rows = session.scalars(select(Site).where(Site.organization_id == organization_id).order_by(Site.site_id.asc())).all()
    return [str(row.site_id) for row in rows]


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()
