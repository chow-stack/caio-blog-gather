from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".avif"}
SKIP_DOMAINS = {"x.com", "twitter.com", "instagram.com", "facebook.com", "youtube.com", "linkedin.com"}


def domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def clean_title(text: str) -> str:
    return " ".join(text.split())


def is_junk(title: str, url: str) -> bool:
    parsed = urlparse(url)
    return (
        not title
        or len(title) < 5
        or title.startswith(("![", "http", "#"))
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or Path(parsed.path).suffix.lower() in IMAGE_EXTS
        or domain(url) in SKIP_DOMAINS
    )


def parse_feed(source: dict, seen: set[str], now: datetime) -> tuple[list[dict], str | None]:
    cutoff = now - timedelta(days=3)
    try:
        feed = feedparser.parse(source["url"])
        if feed.bozo and not feed.entries:
            return [], f"parse error: {feed.bozo_exception}"

        items = []
        for entry in feed.entries:
            url = entry.get("link", "").strip().split("#atom-")[0]
            title = clean_title(entry.get("title", "").strip())
            if not url or url in seen or is_junk(title, url):
                continue

            published = entry.get("published_parsed") or entry.get("updated_parsed")
            published_at = None
            if published:
                published_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if published_dt < cutoff:
                    continue
                published_at = published_dt.isoformat()

            items.append(
                {
                    "title": title,
                    "url": url,
                    "source": source["name"],
                    "source_url": source["url"],
                    "priority": source.get("priority", 5),
                    "date_found": now.date().isoformat(),
                    "pub_date": published_at,
                }
            )
            if len(items) >= source.get("max_articles", 100):
                break
        return items, None
    except Exception as exc:
        return [], str(exc)


def gather(sources: list[dict], seen: set[str], now: datetime) -> tuple[list[dict], list[str]]:
    candidates: list[dict] = []
    errors: list[str] = []

    for source in sources:
        items, error = parse_feed(source, seen, now)
        candidates.extend(items)
        if error:
            errors.append(f"{source['name']}: {error}")

    candidates.sort(key=lambda item: (item.get("priority", 0), item.get("pub_date") or ""), reverse=True)
    # Higher-priority sources win when multiple feeds link to the same article.
    unique = []
    discovered = set(seen)
    for candidate in candidates:
        if candidate["url"] in discovered:
            continue
        unique.append(candidate)
        discovered.add(candidate["url"])
    return unique, errors


def filter_by_recency(candidates: list[dict], hours: int, now: datetime) -> list[dict]:
    cutoff = now - timedelta(hours=hours)
    recent = []
    for candidate in candidates:
        published = candidate.get("pub_date")
        if not published:
            recent.append(candidate)
            continue
        try:
            parsed = datetime.fromisoformat(published)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed >= cutoff:
                recent.append(candidate)
        except ValueError:
            recent.append(candidate)
    return recent
