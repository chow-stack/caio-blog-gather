import json
import re
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import feedparser

from sources import RSS_SOURCES, VAULT_PATH

SCRIPT_DIR = Path(__file__).parent
VAULT = SCRIPT_DIR / VAULT_PATH
CANDIDATES_DIR = SCRIPT_DIR / "candidates"
LOG_FILE = VAULT / "Blog" / "log.md"
SEEN_FILE = SCRIPT_DIR / "seen_urls.json"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".avif"}
SKIP_DOMAINS = {"x.com", "twitter.com", "instagram.com", "facebook.com", "youtube.com", "linkedin.com"}
SKIP_TITLES = {"not much happened today", "see all issues", "issues", "subscribe", "upgrade"}
SKIP_TITLE_WORDS = {"terms", "privacy", "cookie", "report abuse", "powered by", "licensing", "accessibility"}


def load_seen() -> set:
    return set(json.loads(SEEN_FILE.read_text())["urls"])


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps({"urls": sorted(seen)}, indent=2))


def domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def clean_title(text: str, src_domain: str) -> str:
    text = " ".join(text.split())
    if src_domain == "news.smol.ai":
        text = re.sub(r"\s*Show details.*$", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+", "", text).strip()
    return text


def is_junk(title: str, url: str) -> bool:
    if not title or len(title) < 5:
        return True
    if title.lower() in SKIP_TITLES:
        return True
    if title.startswith(("![", "http", "#")):
        return True
    if any(w in title.lower() for w in SKIP_TITLE_WORDS):
        return True
    if Path(urlparse(url).path).suffix.lower() in IMAGE_EXTS:
        return True
    if domain(url) in SKIP_DOMAINS:
        return True
    return False


SMOL_SOURCE = "news.smol.ai"


def parse_rss_feed(feed_url: str, seen: set, today: str) -> tuple[list, str | None]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    is_smol = domain(feed_url) == SMOL_SOURCE
    try:
        feed = feedparser.parse(feed_url)
        if feed.bozo and not feed.entries:
            return [], f"parse error: {feed.bozo_exception}"
        src = domain(feed.get("feed", {}).get("link", feed_url) or feed_url) or domain(feed_url)
        candidates = []
        smol_latest_added = False
        for entry in feed.entries:
            url = entry.get("link", "").strip().split("#atom-")[0]
            title = entry.get("title", "").strip()
            if not url or not title or url in seen:
                continue
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            pub_date_str = None
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                # For smol.ai, always include the single latest entry regardless of age
                if pub_dt < cutoff and not (is_smol and not smol_latest_added):
                    continue
                pub_date_str = pub_dt.isoformat()
            title = clean_title(title, src)
            if is_junk(title, url):
                continue
            candidates.append({"title": title, "url": url, "source": src, "date_found": today, "pub_date": pub_date_str})
            if is_smol:
                smol_latest_added = True
        return candidates, None
    except Exception as e:
        return [], str(e)


def main():
    today = date.today().isoformat()
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)

    seen = load_seen()
    results = [parse_rss_feed(feed_url, seen, today) for feed_url in RSS_SOURCES]

    all_candidates = []
    for candidates, _ in results:
        all_candidates.extend(candidates)
        seen.update(c["url"] for c in candidates)

    out_file = CANDIDATES_DIR / f"{today}.json"
    out_file.write_text(json.dumps(all_candidates, indent=2, ensure_ascii=False))
    save_seen(seen)

    source_sections = []
    for (candidates, error), feed_url in zip(results, RSS_SOURCES):
        src_domain = domain(feed_url)
        header = f"### {src_domain} (RSS) — {len(candidates)}"
        if error:
            header += f" (ERROR: {error})"
        lines = [header] + [f"- [{c['title']}]({c['url']})" for c in candidates]
        source_sections.append("\n".join(lines))

    log_entry = (
        f"\n## {today} | gather | {len(all_candidates)} new candidates\n\n"
        + "\n\n".join(source_sections)
        + "\n"
    )
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(log_entry)

    print(f"Done. {len(all_candidates)} candidates → {out_file}")
    for (_, error), feed_url in zip(results, RSS_SOURCES):
        if error:
            print(f"  ERROR {domain(feed_url)}: {error}")


if __name__ == "__main__":
    main()
