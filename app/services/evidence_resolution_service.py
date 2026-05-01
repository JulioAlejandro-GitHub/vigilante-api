from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel

from app.config import get_settings
from app.services.evidence_ref_classifier import dedupe_evidence_refs, extract_evidence_refs
from app.services.media_client import MediaClient, MediaClientError
from app.services.media_models import EvidenceMediaItem


logger = logging.getLogger(__name__)

T = TypeVar("T")

class EvidenceResolutionService:
    def __init__(self, *, client: MediaClient | None, max_refs: int = 20) -> None:
        self.client = client
        self.max_refs = max(1, max_refs)
        self._cache: dict[str, EvidenceMediaItem] = {}

    def enrich(self, item: T) -> T:
        if isinstance(item, list):
            return self.enrich_list(item)  # type: ignore[return-value]
        if not isinstance(item, BaseModel):
            return item

        updates: dict[str, Any] = {}
        for field_name in ("reviews", "suggestions", "timeline"):
            nested_items = getattr(item, field_name, None)
            if isinstance(nested_items, list):
                updates[field_name] = self.enrich_list(nested_items)

        refs = extract_evidence_refs(item, max_refs=self.max_refs)
        if hasattr(item, "evidence_media"):
            updates["evidence_media"] = self.resolve_refs(refs) if refs else []

        if not updates:
            return item
        return item.model_copy(update=updates)  # type: ignore[return-value]

    def enrich_list(self, items: list[T]) -> list[T]:
        return [self.enrich(item) for item in items]

    def resolve_refs(self, refs: Iterable[str]) -> list[EvidenceMediaItem]:
        unique_refs = dedupe_evidence_refs(refs)[: self.max_refs]
        if not unique_refs:
            return []
        if self.client is None:
            return []

        resolved_items = [self._resolve_ref(ref) for ref in unique_refs]
        if any(not item.resolved for item in resolved_items):
            logger.info("evidence_resolution_partial", extra={"ref_count": len(unique_refs)})
        return resolved_items

    def _resolve_ref(self, ref: str) -> EvidenceMediaItem:
        cached = self._cache.get(ref)
        if cached is not None:
            return cached

        logger.info("media_resolve_requested", extra={"ref_hash": _ref_hash(ref)})
        try:
            asset = self.client.resolve(ref)
        except MediaClientError as exc:
            logger.info(
                "media_service_unavailable" if exc.reason == "media_service_unavailable" else "media_resolve_failed",
                extra={"ref_hash": _ref_hash(ref), "reason": exc.reason, "status_code": exc.status_code},
            )
            item = EvidenceMediaItem.unresolved(ref=ref, error=exc.reason)
        else:
            logger.info("media_resolve_succeeded", extra={"ref_hash": _ref_hash(ref), "media_id": asset.media_id})
            item = EvidenceMediaItem.from_asset(ref, asset)

        self._cache[ref] = item
        return item


def evidence_resolution_service_dependency() -> EvidenceResolutionService:
    settings = get_settings()
    return EvidenceResolutionService(
        client=MediaClient.from_settings(settings),
        max_refs=settings.media_resolution_max_refs,
    )


def _ref_hash(ref: str) -> str:
    return hashlib.sha256(ref.encode("utf-8")).hexdigest()[:12]
