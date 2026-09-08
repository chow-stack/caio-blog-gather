from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from google import genai
from google.genai import errors as genai_errors, types

from digest.constants import ALL_SECTIONS


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    return re.sub(r"\s*```$", "", text).strip()


def call_model(client: genai.Client, prompt: str, models: list[str]) -> tuple[str, str]:
    config = types.GenerateContentConfig(response_mime_type="application/json")
    for model in models:
        for attempt in range(3):
            try:
                chat = client.chats.create(model=model, config=config)
                response = chat.send_message(prompt)
                return response.text, model
            except genai_errors.APIError as exc:
                if exc.code not in (429, 503):
                    raise
                wait = 2 ** (attempt + 1)
                print(f"  {exc.code} on {model}; retrying in {wait}s")
                time.sleep(wait)
        print(f"  {model} unavailable; trying fallback")
    raise RuntimeError("All configured models were unavailable")


def select_articles(
    candidates: list[dict], client: genai.Client, prompt_path: Path, models: list[str], max_articles: int
) -> tuple[list[dict], str | None]:
    if not candidates:
        return [], None
    slim = [
        {"title": item["title"], "url": item["url"], "source": item["source"]}
        for item in candidates
    ]
    prompt = prompt_path.read_text().format(
        candidates_json=json.dumps(slim, indent=2, ensure_ascii=False),
        max_articles=max_articles,
    )
    text, model = call_model(client, prompt, models)
    selected = json.loads(strip_fences(text))
    allowed = {item["url"]: item for item in candidates}
    # Keep model choices, but restore trusted metadata and enforce the configured limit.
    return [allowed[item["url"]] for item in selected if item.get("url") in allowed][:max_articles], model


async def _scrape_one(crawler, run_config, item: dict) -> dict:
    try:
        result = await crawler.arun(url=item["url"], config=run_config)
        if result.success:
            markdown = result.markdown
            content = (
                markdown.fit_markdown or markdown.raw_markdown
                if hasattr(markdown, "fit_markdown")
                else markdown or ""
            )
            return {**item, "content": content[:8000]}
    except Exception as exc:
        print(f"  Could not scrape {item['url']}: {exc}")
    return {**item, "content": None}


async def scrape_articles(items: list[dict]) -> list[dict]:
    if not items:
        return []
    browser_config = BrowserConfig(headless=True, enable_stealth=True)
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, scan_full_page=True)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        return list(await asyncio.gather(*[_scrape_one(crawler, run_config, item) for item in items]))


def build_digest(
    scraped: list[dict], client: genai.Client, prompt_path: Path, models: list[str], max_top_stories: int
) -> tuple[dict, str]:
    articles = []
    for index, item in enumerate(scraped, 1):
        content = item.get("content") or "[Content could not be retrieved]"
        articles.append(
            f"ARTICLE {index}: {item['title']}\nURL: {item['url']}\nSOURCE: {item['source']}\n{content}"
        )
    prompt = prompt_path.read_text().format(
        article_count=len(scraped),
        max_top_stories=max_top_stories,
        articles_text="\n\n---\n\n".join(articles),
    )
    text, model = call_model(client, prompt, models)
    return json.loads(strip_fences(text)), model


def validate_digest(digest: dict, scraped: list[dict], max_top_stories: int) -> tuple[dict, int]:
    allowed = {item["url"]: item for item in scraped}
    used: set[str] = set()
    for section in ALL_SECTIONS:
        cleaned = []
        for item in digest.get(section, []):
            url = item.get("url", "") if isinstance(item, dict) else ""
            if url not in allowed or url in used:
                continue
            item["url"] = allowed[url]["url"]
            item["source"] = allowed[url]["source"]
            item.setdefault("title", allowed[url]["title"])
            item.setdefault("what", "See the article for details.")
            item.setdefault("bullets", [])
            cleaned.append(item)
            used.add(url)
        digest[section] = cleaned

    overflow = digest["top_stories"][max_top_stories:]
    digest["top_stories"] = digest["top_stories"][:max_top_stories]
    digest["important_developments"] = overflow + digest["important_developments"]

    missing = [item for item in scraped if item["url"] not in used]
    digest["other_notable"].extend(
        {
            "title": item["title"],
            "url": item["url"],
            "source": item["source"],
            "what": "See the article for details.",
            "bullets": [],
        }
        for item in missing
    )
    return digest, len(missing)
