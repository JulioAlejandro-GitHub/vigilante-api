from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


ENCRYPTED_SECRET_PREFIX = "fernet:v1:"


class CameraSecretError(ValueError):
    pass


def is_encrypted_camera_secret(value: str | None) -> bool:
    return bool(value) and str(value).startswith(ENCRYPTED_SECRET_PREFIX)


def is_legacy_plain_camera_secret(value: str | None) -> bool:
    return bool(value) and not is_encrypted_camera_secret(value)


def encrypt_camera_secret_if_plaintext(value: str | None) -> str | None:
    if value in (None, ""):
        return value
    if is_encrypted_camera_secret(value):
        return value
    return encrypt_camera_secret(value)


def encrypt_camera_secret(plaintext: str) -> str:
    if not plaintext:
        raise CameraSecretError("camera_secret cannot be empty")
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_SECRET_PREFIX}{token}"


def decrypt_camera_secret(value: str | None, *, allow_legacy_plaintext: bool = True) -> str | None:
    if value in (None, ""):
        return None
    if not is_encrypted_camera_secret(value):
        if allow_legacy_plaintext:
            return value
        raise CameraSecretError("camera_secret is not encrypted")
    token = value[len(ENCRYPTED_SECRET_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise CameraSecretError("camera_secret could not be decrypted") from exc


def _fernet() -> Fernet:
    key = get_settings().camera_secret_fernet_key
    if not key:
        raise CameraSecretError("CAMERA_SECRET_FERNET_KEY is required to encrypt or decrypt camera_secret")
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise CameraSecretError("CAMERA_SECRET_FERNET_KEY must be a valid Fernet key") from exc
