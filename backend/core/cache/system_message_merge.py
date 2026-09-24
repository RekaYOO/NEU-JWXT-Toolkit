"""Consistency helpers for the system-message cache."""

from __future__ import annotations

from typing import Any


def preserve_system_message_read_state(
    previous: Any,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Keep a locally acknowledged message from regressing to unread.

    The official read endpoint and a background cache refresh can complete in
    either order. Read state is monotonic for the message center, so an
    already-read cached row must not be overwritten by an older remote
    snapshot returned by a refresh that started before the acknowledgement.
    """
    previous_rows = {
        (str(item.get("kind") or "reminder"), str(item.get("id") or "")): item
        for item in (previous or {}).get("messages") or []
        if isinstance(item, dict) and item.get("id")
    }
    merged_rows = []
    for item in current.get("messages") or []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("kind") or "reminder"), str(item.get("id") or ""))
        previous_item = previous_rows.get(key)
        if previous_item and previous_item.get("read") is True and item.get("read") is not True:
            item = {**item, "read": True}
        merged_rows.append(item)
    return {**current, "messages": merged_rows}
