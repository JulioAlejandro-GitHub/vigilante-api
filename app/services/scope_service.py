from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


def require_scope_access(
    user,
    *,
    organization_id: str | None,
    site_id: str | None,
    operate: bool = False,
    admin: bool = False,
) -> None:
    if not scope_allows(user, organization_id=organization_id, site_id=site_id, operate=operate, admin=admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Resource is outside the authenticated user's scope",
        )


def scope_allows(
    user,
    *,
    organization_id: str | None,
    site_id: str | None,
    operate: bool = False,
    admin: bool = False,
) -> bool:
    normalized_org = _normalize(organization_id)
    normalized_site = _normalize(site_id)
    if normalized_org is None and normalized_site is None:
        return False

    for scope in user.scopes:
        if admin and not scope.can_admin:
            continue
        if operate and not (scope.can_operate or scope.can_admin):
            continue
        if normalized_org and normalized_org == scope.organization_id:
            if normalized_site is None or scope.all_sites or normalized_site in set(scope.site_ids):
                return True
        if normalized_site and normalized_site in set(scope.site_ids):
            return True
    return False


def filter_items_by_scope(user, items: list[Any]) -> list[Any]:
    return [
        item
        for item in items
        if scope_allows(
            user,
            organization_id=_item_organization_id(item),
            site_id=_item_site_id(item),
        )
    ]


def require_item_scope(user, item: Any, *, operate: bool = False, admin: bool = False) -> None:
    require_scope_access(
        user,
        organization_id=_item_organization_id(item),
        site_id=_item_site_id(item),
        operate=operate,
        admin=admin,
    )


def _item_organization_id(item: Any) -> str | None:
    direct = _normalize(getattr(item, "organization_id", None))
    if direct:
        return direct
    payload = getattr(item, "case_payload", None)
    if isinstance(payload, dict):
        return _normalize(payload.get("raw_organization_id"))
    return None


def _item_site_id(item: Any) -> str | None:
    direct = _normalize(getattr(item, "site_id", None))
    if direct:
        return direct
    payload = getattr(item, "case_payload", None)
    if isinstance(payload, dict):
        return _normalize(payload.get("raw_site_id"))
    return None


def _normalize(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
