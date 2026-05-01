from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel


EVIDENCE_REF_KEYS = {
    "evidence_ref",
    "evidence_refs",
    "frame_ref",
    "frame_refs",
    "frame_uri",
    "frame_uris",
    "image_ref",
    "image_refs",
    "image_uri",
    "image_uris",
    "media_ref",
    "media_refs",
    "media_uri",
    "media_uris",
    "source_frame_ref",
    "source_frame_refs",
}
FIXTURE_EVIDENCE_REF_PREFIXES = ("tests/fixtures/",)
LIVE_EVIDENCE_REF_PREFIXES = ("s3://", "minio://")


@dataclass(frozen=True)
class EvidenceRefProfile:
    refs: list[str]
    has_live: bool
    has_fixture: bool
    is_fixture_only: bool


def is_fixture_evidence_ref(ref: str) -> bool:
    normalized = ref.strip()
    return any(normalized.startswith(prefix) for prefix in FIXTURE_EVIDENCE_REF_PREFIXES)


def is_live_evidence_ref(ref: str) -> bool:
    normalized = ref.strip().lower()
    return any(normalized.startswith(prefix) for prefix in LIVE_EVIDENCE_REF_PREFIXES)


def evidence_ref_profile(item: BaseModel | dict[str, Any] | Any, *, max_refs: int = 20) -> EvidenceRefProfile:
    refs = extract_evidence_refs(item, max_refs=max_refs)
    has_live = any(is_live_evidence_ref(ref) for ref in refs)
    has_fixture = any(is_fixture_evidence_ref(ref) for ref in refs)
    return EvidenceRefProfile(
        refs=refs,
        has_live=has_live,
        has_fixture=has_fixture,
        is_fixture_only=bool(refs) and all(is_fixture_evidence_ref(ref) for ref in refs),
    )


def extract_evidence_refs(item: BaseModel | dict[str, Any] | Any, *, max_refs: int = 20) -> list[str]:
    refs: list[str] = []
    roots = _evidence_roots(item)
    for root in roots:
        _collect_refs(root, refs, max_refs=max_refs)
        if len(refs) >= max_refs:
            break
    return dedupe_evidence_refs(refs)[:max_refs]


def dedupe_evidence_refs(refs: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return deduped


def _evidence_roots(item: BaseModel | dict[str, Any] | Any) -> list[Any]:
    if isinstance(item, BaseModel):
        roots = []
        for attribute in ("payload", "case_payload", "resolution_payload"):
            if hasattr(item, attribute):
                roots.append(getattr(item, attribute))
        return roots
    return [item]


def _collect_refs(value: Any, refs: list[str], *, max_refs: int) -> None:
    if len(refs) >= max_refs:
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).lower()
            if normalized_key in EVIDENCE_REF_KEYS:
                _append_ref_values(nested, refs, max_refs=max_refs)
            _collect_refs(nested, refs, max_refs=max_refs)
            if len(refs) >= max_refs:
                return
        return
    if isinstance(value, list):
        for nested in value:
            _collect_refs(nested, refs, max_refs=max_refs)
            if len(refs) >= max_refs:
                return


def _append_ref_values(value: Any, refs: list[str], *, max_refs: int) -> None:
    if len(refs) >= max_refs:
        return
    if isinstance(value, str):
        if _valid_ref(value):
            refs.append(value)
        return
    if isinstance(value, dict):
        for nested in value.values():
            _append_ref_values(nested, refs, max_refs=max_refs)
            if len(refs) >= max_refs:
                return
        return
    if isinstance(value, list):
        for nested in value:
            _append_ref_values(nested, refs, max_refs=max_refs)
            if len(refs) >= max_refs:
                return


def _valid_ref(value: str) -> bool:
    return bool(value.strip()) and len(value) <= 4096 and not any(ord(char) < 32 for char in value)
