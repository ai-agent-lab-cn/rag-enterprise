from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class RetrievalAccessContext:
    user_id: str


def metadata_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value]
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


def can_retrieve_metadata(
    metadata: dict[str, Any],
    access: RetrievalAccessContext | None,
    now: datetime | None = None,
) -> bool:
    """统一治理边界：状态与有效期始终生效，Deny 优先于 Allow。"""

    if metadata.get("retrieval_status", "searchable") != "searchable":
        return False
    current = now or datetime.now(UTC)
    valid_from = _timestamp(metadata.get("valid_from"))
    valid_to = _timestamp(metadata.get("valid_to"))
    if valid_from and current < valid_from:
        return False
    if valid_to and current > valid_to:
        return False
    if access is None:
        return True
    if not _acl_allows(metadata, access.user_id):
        return False
    source_acl = metadata.get("data_source_acl") or {}
    if isinstance(source_acl, str):
        try:
            source_acl = json.loads(source_acl)
        except json.JSONDecodeError:
            return False
    return _acl_allows(source_acl, access.user_id)


def _acl_allows(policy: dict[str, Any], user_id: str) -> bool:
    denied = set(metadata_list(policy.get("deny_user_ids")))
    if user_id in denied:
        return False
    allowed = set(metadata_list(policy.get("allow_user_ids")))
    return not allowed or user_id in allowed


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
