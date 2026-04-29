from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import Settings
from app.services.media_models import MediaAssetResponse


class MediaClientError(Exception):
    def __init__(self, reason: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


class MediaClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        public_base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.public_base_url = (public_base_url or base_url).rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> "MediaClient | None":
        if not settings.media_service_base_url:
            return None
        return cls(
            base_url=settings.media_service_base_url,
            timeout_seconds=settings.media_service_timeout_seconds,
            public_base_url=settings.media_service_public_base_url,
        )

    def resolve(self, ref: str) -> MediaAssetResponse:
        if not _safe_ref(ref):
            raise MediaClientError("invalid_media_reference", "Media reference is empty or contains invalid characters.")

        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
                response = client.get("/api/v1/media/resolve", params={"ref": ref})
        except httpx.TimeoutException as exc:
            raise MediaClientError("media_service_unavailable", "Media service timed out.") from exc
        except httpx.HTTPError as exc:
            raise MediaClientError("media_service_unavailable", "Media service is unavailable.") from exc

        if response.status_code >= 400:
            reason = _error_reason(response)
            raise MediaClientError(reason, "Media service could not resolve reference.", status_code=response.status_code)

        try:
            asset = MediaAssetResponse.model_validate(response.json())
        except ValueError as exc:
            raise MediaClientError("invalid_media_response", "Media service returned an invalid response.") from exc

        asset.content_url = self._public_url(asset.content_url)
        asset.metadata_url = asset.metadata_url or self._metadata_url(asset)
        return asset

    def _metadata_url(self, asset: MediaAssetResponse) -> str | None:
        if not asset.media_id:
            return None
        return self._public_url(f"/api/v1/media/{asset.media_id}")

    def _public_url(self, url: str | None) -> str | None:
        if not url:
            return None
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return urljoin(f"{self.public_base_url}/", url.lstrip("/"))


def _error_reason(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return f"media_service_http_{response.status_code}"

    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        error = detail.get("error")
        if error:
            return str(error)
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return f"media_service_http_{response.status_code}"


def _safe_ref(ref: str) -> bool:
    if not ref or len(ref) > 4096:
        return False
    return not any(ord(char) < 32 for char in ref)
