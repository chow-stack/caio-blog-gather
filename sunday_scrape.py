"""
Step 2 of the Sunday blog build.
Reads candidates/selected.json (written by Claude after filtering),
scrapes each article, writes raw files to Blog/raw/.
Claude then processes Blog/raw/ → Blog/wiki/sources/ and writes the draft outline.
"""

import asyncio
import json
import re
from datetime import date, timedelta
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from sources import VAULT_PATH

SCRIPT_DIR = Path(__file__).parent
VAULT = SCRIPT_DIR / VAULT_PATH
SELECTED_FILE = SCRIPT_DIR / "candidates" / "selected.json"
RAW_DIR = VAULT / "Blog" / "raw"
LOG_FILE = VAULT / "Blog" / "log.md"
TEMPLATE_FILE = VAULT / "Archive" / "templates" / "source.md"


def slugify(title: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return re.sub(r"-+", "-", slug)[:max_len].rstrip("-")


def week_bounds() -> tuple[str, str]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def yaml_str(value: str) -> str:
    if any(c in value for c in (':', "'", '"', '#', '[')):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def render_source(template: str, item: dict, full_text: str) -> str:
    filled = template.replace("title:", f"title: {yaml_str(item['title'])}")
    filled = filled.replace("url:", f"url: {item['url']}")
    filled = filled.replace("date:", f"date: {item['date_found']}")
    filled = filled.replace("source:", f"source: {item['source']}")
    filled = filled.replace("tags:", "tags: [ai, blog]")
    filled = re.sub(
        r"## Full text\n\n<!--.*?-->",
        lambda m: f"## Full text\n\n{full_text}",
        filled,
        flags=re.DOTALL,
    )
    return filled


async def scrape_item(
    crawler: AsyncWebCrawler,
    run_config: CrawlerRunConfig,
    item: dict,
    template: str,
) -> tuple[str | None, str | None]:
    slug = slugify(item["title"])
    out_path = RAW_DIR / f"{item['date_found']}-{slug}.md"
    try:
        result = await crawler.arun(url=item["url"], config=run_config)
        if not result.success:
            raise RuntimeError("crawl returned success=False")
        md = result.markdown
        full_text = (md.fit_markdown or md.raw_markdown) if hasattr(md, "fit_markdown") else (md or "")
        content = render_source(template, item, full_text)
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        return slug, None
    except Exception as e:
        return None, f"{item['title'][:60]}: {e}"


async def main():
    if not SELECTED_FILE.exists():
        print(f"ERROR: {SELECTED_FILE} not found. Run sunday_prepare.py first, then have Claude write selected.json.")
        return

    items = json.loads(SELECTED_FILE.read_text())
    if not items:
        print("selected.json is empty.")
        return

    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    today = date.today().isoformat()

    print(f"Scraping {len(items)} selected articles...\n")

    browser_config = BrowserConfig(headless=True, enable_stealth=True)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed")
        ),
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        tasks = [scrape_item(crawler, run_config, item, template) for item in items]
        results = await asyncio.gather(*tasks)

    successful = [(item, slug) for (slug, _), item in zip(results, items) if slug]
    errors = [err for _, err in results if err]

    titles = ", ".join(item["title"][:60] for item, _ in successful)
    log_entry = f"\n## {today} | sunday-scrape | {len(successful)} raw files written to Blog/raw/\n- Items: {titles}\n"
    if errors:
        log_entry += "\n".join(f"- ERROR: {e}" for e in errors) + "\n"

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(log_entry)

    print(f"\nDone. {len(successful)} raw files written to {RAW_DIR}")
    print("\nRaw files created:")
    for item, slug in successful:
        print(f"  - {item['date_found']}-{slug}.md")

    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  {e}")

    SELECTED_FILE.unlink(missing_ok=True)
    (SCRIPT_DIR / "candidates" / "this_week.json").unlink(missing_ok=True)
    for f in (SCRIPT_DIR / "candidates").glob("????-??-??.json"):
        f.unlink(missing_ok=True)

    print(f"\nNext: Claude processes Blog/raw/ → Blog/wiki/sources/ and writes the draft outline.")


if __name__ == "__main__":
    asyncio.run(main())
