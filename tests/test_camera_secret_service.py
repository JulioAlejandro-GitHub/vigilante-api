from __future__ import annotations

import pytest

from app.services.camera_secret_service import (
    CameraSecretError,
    decrypt_camera_secret,
    encrypt_camera_secret,
    encrypt_camera_secret_if_plaintext,
    is_encrypted_camera_secret,
    is_legacy_plain_camera_secret,
)


def test_camera_secret_encrypts_and_decrypts() -> None:
    encrypted = encrypt_camera_secret("admin123")

    assert encrypted != "admin123"
    assert encrypted.startswith("fernet:v1:")
    assert is_encrypted_camera_secret(encrypted)
    assert decrypt_camera_secret(encrypted) == "admin123"


def test_camera_secret_helper_preserves_encrypted_values() -> None:
    encrypted = encrypt_camera_secret("admin123")

    assert encrypt_camera_secret_if_plaintext(encrypted) == encrypted


def test_camera_secret_treats_plaintext_as_legacy_only_when_allowed() -> None:
    assert is_legacy_plain_camera_secret("admin123")
    assert decrypt_camera_secret("admin123", allow_legacy_plaintext=True) == "admin123"

    with pytest.raises(CameraSecretError, match="not encrypted"):
        decrypt_camera_secret("admin123", allow_legacy_plaintext=False)


def test_camera_secret_reports_invalid_ciphertext_without_leaking_value() -> None:
    with pytest.raises(CameraSecretError) as exc:
        decrypt_camera_secret("fernet:v1:not-a-valid-token")

    assert str(exc.value) == "camera_secret could not be decrypted"
    assert "not-a-valid-token" not in str(exc.value)
