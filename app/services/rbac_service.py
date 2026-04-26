from __future__ import annotations

from fastapi import HTTPException, status


SUPERVISOR_ROLES = {"supervisor", "admin"}
ANALYST_ROLES = {"analyst", "operator", "reviewer"} | SUPERVISOR_ROLES
READ_ROLES = ANALYST_ROLES | {"auditor"}


def canonical_role(roles: list[str]) -> str:
    normalized = {role.lower() for role in roles}
    if normalized & SUPERVISOR_ROLES:
        return "supervisor"
    if normalized & ANALYST_ROLES:
        return "analyst"
    if "auditor" in normalized:
        return "auditor"
    return sorted(normalized)[0] if normalized else "none"


def is_supervisor(user) -> bool:
    return bool(set(user.roles) & SUPERVISOR_ROLES)


def is_analyst_or_above(user) -> bool:
    return bool(set(user.roles) & ANALYST_ROLES)


def can_read_sensitive(user) -> bool:
    return bool(set(user.roles) & READ_ROLES)


def require_sensitive_read(user) -> None:
    if not can_read_sensitive(user):
        _deny("Authenticated user does not have read access")


def require_analyst(user) -> None:
    if not is_analyst_or_above(user):
        _deny("Analyst role is required")


def require_supervisor(user) -> None:
    if not is_supervisor(user):
        _deny("Supervisor role is required")


def require_case_assignment_permission(user, *, assigned_to: str, previous_assigned_to: str | None) -> None:
    if is_supervisor(user):
        return
    if assigned_to == user.username and previous_assigned_to in {None, user.username} and is_analyst_or_above(user):
        return
    _deny("Supervisor role is required to assign or reassign this case")


def require_case_unassignment_permission(user, *, previous_assigned_to: str | None) -> None:
    if is_supervisor(user):
        return
    if previous_assigned_to == user.username and is_analyst_or_above(user):
        return
    _deny("Supervisor role is required to unassign this case")


def _deny(detail: str) -> None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
