from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_seen(path: Path, now: datetime) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if "seen" in data:
        return data["seen"]
    # One-time compatibility with the original {"urls": [...]} format.
    return {url: now.isoformat() for url in data.get("urls", [])}


def save_seen(path: Path, seen: dict[str, str], new_urls: list[str], now: datetime, retention_days: int) -> None:
    updated = {**seen, **{url: now.isoformat() for url in new_urls}}
    cutoff = now - timedelta(days=retention_days)
    retained = {}
    for url, timestamp in updated.items():
        try:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed >= cutoff:
                retained[url] = timestamp
        except (TypeError, ValueError):
            retained[url] = now.isoformat()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": dict(sorted(retained.items()))}, indent=2) + "\n")
