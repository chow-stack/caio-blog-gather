import asyncio
import json
import re
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import feedparser
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from sources import SOURCES, SOURCE_CONFIG, RSS_SOURCES, VAULT_PATH

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
    text = " ".join(text.split())  # normalize whitespace
    if src_domain == "news.smol.ai":
        # "Apr 23 GPT 5.5 Show details" → "GPT 5.5"
        text = re.sub(r"\s*Show details.*$", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+", "", text).strip()
    elif src_domain == "aibreakfast.beehiiv.com":
        # "Apr 24, 2026•5 min readTitle hereAI Breakfast" → "Title here"
        text = re.sub(r"^[A-Z][a-z]+ \d+,?\s*\d{4}[•·\s]+\d+\s*min\s*read\s*\d*", "", text).strip()
        text = re.sub(r"AI Breakfast$", "", text).strip()
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
    parsed = urlparse(url)
    if Path(parsed.path).suffix.lower() in IMAGE_EXTS:
        return True
    if domain(url) in SKIP_DOMAINS:
        return True
    return False


def extract_links(result, src_url: str) -> list[dict]:
    src_domain = domain(src_url)
    cfg = SOURCE_CONFIG.get(src_domain, {"link_type": "external"})
    link_type = cfg.get("link_type", "external")
    url_contains = cfg.get("url_contains", "")
    url_pattern = cfg.get("url_pattern", "")

    raw = result.links.get(link_type, [])
    seen_hrefs: set[str] = set()
    items = []

    for link in raw:
        href = link.get("href", "").strip()
        raw_text = (link.get("text") or link.get("title") or "").strip()

        if not href or href in seen_hrefs:
            continue
        if url_contains and url_contains not in href:
            continue
        if url_pattern and not re.search(url_pattern, href):
            continue

        title = clean_title(raw_text, src_domain)
        if is_junk(title, href):
            continue

        seen_hrefs.add(href)
        items.append({"title": title, "url": href})

    return items


def parse_rss_feed(feed_url: str, seen: set, today: str) -> tuple[list, str | None]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    try:
        feed = feedparser.parse(feed_url)
        if feed.bozo and not feed.entries:
            return [], f"parse error: {feed.bozo_exception}"
        src = domain(feed.get("feed", {}).get("link", feed_url) or feed_url) or domain(feed_url)
        candidates = []
        for entry in feed.entries:
            url = entry.get("link", "").strip().split("#atom-")[0]
            title = entry.get("title", "").strip()
            if not url or not title or url in seen:
                continue
            # parse published date; skip entries older than 3 days
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            if is_junk(title, url):
                continue
            candidates.append({"title": title, "url": url, "source": src, "date_found": today})
        return candidates, None
    except Exception as e:
        return [], str(e)


async def crawl_source(
    crawler: AsyncWebCrawler,
    run_config: CrawlerRunConfig,
    source_url: str,
    seen: set,
    today: str,
) -> tuple[list, str | None]:
    try:
        result = await crawler.arun(url=source_url, config=run_config)
        if not result.success:
            return [], "crawl returned success=False"
        links = extract_links(result, source_url)
        candidates = [
            {"title": lnk["title"], "url": lnk["url"], "source": domain(source_url), "date_found": today}
            for lnk in links
            if lnk["url"] not in seen
        ]
        return candidates, None
    except Exception as e:
        return [], str(e)


async def main():
    today = date.today().isoformat()
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)

    seen = load_seen()

    # --- RSS feeds (synchronous, fast) ---
    rss_results = [parse_rss_feed(feed_url, seen, today) for feed_url in RSS_SOURCES]
    rss_candidates = []
    for candidates, _ in rss_results:
        rss_candidates.extend(candidates)
        seen.update(c["url"] for c in candidates)

    # --- HTML sources (async crawl4ai) ---
    browser_config = BrowserConfig(headless=True, enable_stealth=True)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        scan_full_page=True,
        remove_consent_popups=True,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        tasks = [crawl_source(crawler, run_config, src, seen, today) for src in SOURCES]
        html_results = await asyncio.gather(*tasks)

    html_candidates = []
    for (candidates, _), src in zip(html_results, SOURCES):
        html_candidates.extend(candidates)
        seen.update(c["url"] for c in candidates)

    all_candidates = rss_candidates + html_candidates

    out_file = CANDIDATES_DIR / f"{today}.json"
    out_file.write_text(json.dumps(all_candidates, indent=2, ensure_ascii=False))
    save_seen(seen)

    # --- log ---
    source_sections = []

    for (candidates, error), feed_url in zip(rss_results, RSS_SOURCES):
        src_domain = domain(feed_url)
        header = f"### {src_domain} (RSS) — {len(candidates)}"
        if error:
            header += f" (ERROR: {error})"
        lines = [header]
        for c in candidates:
            lines.append(f"- [{c['title']}]({c['url']})")
        source_sections.append("\n".join(lines))

    for (candidates, error), src in zip(html_results, SOURCES):
        src_domain = domain(src)
        header = f"### {src_domain} — {len(candidates)}"
        if error:
            header += f" (ERROR: {error})"
        lines = [header]
        for c in candidates:
            lines.append(f"- [{c['title']}]({c['url']})")
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
    for (_, error), feed_url in zip(rss_results, RSS_SOURCES):
        if error:
            print(f"  ERROR {domain(feed_url)}: {error}")
    for (_, error), src in zip(html_results, SOURCES):
        if error:
            print(f"  ERROR {domain(src)}: {error}")


if __name__ == "__main__":
    asyncio.run(main())
