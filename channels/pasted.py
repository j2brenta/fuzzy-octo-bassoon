"""Metadata from Telegram Desktop copy headers; never rewrite source text."""

import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from channels.seed_raw import parse_telegram_desktop


def telegram_copy_metadata(text: str, timezone_name: str | None = None) -> dict | None:
    name = (
        timezone_name
        if timezone_name is not None
        else os.getenv("TELEGRAM_COPY_TIMEZONE", "").strip()
    )
    try:
        zone = ZoneInfo(name) if name else None
    except ZoneInfoNotFoundError:
        raise ValueError(
            "Telegram copy timezone must be a valid IANA timezone."
        ) from None
    try:
        messages = parse_telegram_desktop(text, zone)
    except ValueError:
        return None  # Preserve malformed source without guessing metadata.
    if not messages:
        return None
    rows = []
    for message in messages:
        timestamp = message["captured_at"]
        rows.append(
            {
                "sender_hint": message["forwarded_by"],
                "copied_at_local": timestamp.replace(tzinfo=None).isoformat(),
                "copied_at": timestamp.isoformat() if zone else None,
                "timezone": name or None,
            }
        )
    return {
        "format": "telegram_desktop",
        "messages": rows,
        "original_text_preserved": True,
        "timestamp_meaning": "chat_message_time_not_verified_original_publication",
    }
