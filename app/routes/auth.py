from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import session_dependency
from app.services.auth_service import InactiveUserError, InvalidCredentialsError, authenticate_user, get_user_roles, user_username
from app.services.current_user_service import CurrentUser, build_current_user, get_current_user
from app.services.token_service import create_access_token


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AuthScopeSummary(BaseModel):
    organization_ids: list[str]
    site_ids: list[str]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: CurrentUser
    roles: list[str]
    scope: AuthScopeSummary


class LogoutResponse(BaseModel):
    status: str
    message: str


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, session: Session = Depends(session_dependency)) -> LoginResponse:
    try:
        user = authenticate_user(session, username=request.username, password=request.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive") from exc

    roles = get_user_roles(session, user.user_id)
    token, expires_at = create_access_token(user_id=str(user.user_id), username=user_username(user), roles=roles)
    current_user = build_current_user(session, user)
    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user=current_user,
        roles=current_user.roles,
        scope=AuthScopeSummary(
            organization_ids=current_user.organization_ids,
            site_ids=current_user.site_ids,
        ),
    )


@router.get("/me", response_model=CurrentUser)
def me(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return current_user


@router.post("/logout", response_model=LogoutResponse)
def logout(current_user: CurrentUser = Depends(get_current_user)) -> LogoutResponse:
    return LogoutResponse(
        status="ok",
        message="JWT sessions are stateless in this slice; clients log out by discarding the bearer token.",
    )
