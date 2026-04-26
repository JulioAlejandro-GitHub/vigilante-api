from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.config import get_settings


class TokenError(ValueError):
    pass


def create_access_token(
    *,
    user_id: str,
    username: str,
    roles: list[str],
    now: datetime | None = None,
) -> tuple[str, datetime]:
    settings = get_settings()
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.auth_token_ttl_minutes)
    payload = {
        "iss": settings.auth_token_issuer,
        "sub": user_id,
        "username": username,
        "roles": roles,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid4()),
    }
    return encode_jwt(payload), expires_at


def encode_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join([_json_b64(header), _json_b64(payload)])
    signature = _sign(signing_input)
    return f"{signing_input}.{signature}"


def decode_access_token(token: str, *, now: datetime | None = None) -> dict[str, Any]:
    try:
        header_b64, payload_b64, signature = token.split(".", 2)
    except ValueError as exc:
        raise TokenError("Invalid token format") from exc

    signing_input = f"{header_b64}.{payload_b64}"
    expected_signature = _sign(signing_input)
    if not hmac.compare_digest(signature, expected_signature):
        raise TokenError("Invalid token signature")

    header = _decode_json_b64(header_b64)
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise TokenError("Unsupported token header")

    payload = _decode_json_b64(payload_b64)
    settings = get_settings()
    if payload.get("iss") != settings.auth_token_issuer:
        raise TokenError("Invalid token issuer")
    subject = payload.get("sub")
    if not subject:
        raise TokenError("Token subject is missing")
    expires_at = payload.get("exp")
    if not isinstance(expires_at, int):
        raise TokenError("Token expiration is missing")
    current_ts = int((now or datetime.now(timezone.utc)).timestamp())
    if expires_at <= current_ts:
        raise TokenError("Token has expired")
    return payload


def _sign(signing_input: str) -> str:
    digest = hmac.new(
        get_settings().token_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64encode(digest)


def _json_b64(value: dict[str, Any]) -> str:
    return _b64encode(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))


def _decode_json_b64(value: str) -> dict[str, Any]:
    try:
        decoded = base64.urlsafe_b64decode(_pad_b64(value)).decode("utf-8")
        parsed = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("Invalid token payload") from exc
    if not isinstance(parsed, dict):
        raise TokenError("Invalid token payload")
    return parsed


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _pad_b64(value: str) -> bytes:
    return (value + ("=" * (-len(value) % 4))).encode("ascii")
