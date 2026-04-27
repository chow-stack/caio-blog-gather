import asyncio
import json
import os
import re
import smtplib
from datetime import date, datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google import genai
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

SCRIPT_DIR = Path(__file__).parent
CANDIDATES_DIR = SCRIPT_DIR / "candidates"

EMAIL_FROM = "cdesouza202@gmail.com"
EMAIL_TO = "cdesouza202@gmail.com"
SCRAPE_TOP_N = 15
MODEL = "gemini-2.5-flash"

# Call 1: titles only → select what's worth scraping
SELECT_PROMPT = """\
You are selecting AI news articles to read in full for Caio, a college student who builds with AI \
and writes a weekly blog.

He cares about: new model releases, tool features (Claude, ChatGPT, Cursor, Gemini, etc.), \
agent workflows, prompt techniques, open-source releases runnable on consumer hardware, \
surprising or non-obvious AI developments.

Skip: funding rounds, acquisitions, valuations, vague research with no demo, \
enterprise-only tools, generic opinion pieces.

Candidates:
{candidates_json}

Return the {top_n} most worth reading in full as JSON only — no markdown:
[{{"title": "...", "url": "...", "source": "..."}}]"""

# Call 2: full content → complete structured digest in one shot
DIGEST_PROMPT = """\
You are building a daily AI digest for Caio, a college student who builds with AI and writes a weekly blog.

Read these articles and return a single JSON object with this exact structure:
{{
  "tldr": ["bullet 1", "bullet 2", "bullet 3"],
  "top_stories": [
    {{"title": "short clean title", "url": "...", "source": "...", \
"what": "1 sentence — what happened", "why": "1 sentence — why it matters to builders"}}
  ],
  "important_developments": [
    {{"title": "short clean title", "url": "...", "source": "...", "what": "1 sentence"}}
  ],
  "builder_tools": [
    {{"title": "short clean title", "url": "...", "source": "...", "what": "1 sentence"}}
  ],
  "market_signals": [
    {{"title": "short clean title", "url": "...", "source": "...", "what": "1 sentence"}}
  ]
}}

Rules:
- tldr: 3-4 bullets on the day's themes — specific and high-signal ("GPT-5 released" not "big news today")
- top_stories: max 5, most impactful/actionable — must have both what + why
- important_developments: notable but not top-tier
- builder_tools: tools, APIs, open-source, prompt techniques usable today
- market_signals: company moves worth tracking — keep minimal, skip pure funding/valuation news
- every article goes in exactly one category, or is dropped if not worth including
- shorten and clean titles — remove clickbait, trim length
- return JSON only, no markdown, no explanation

Articles:
{articles_text}"""


def load_candidates(today: str) -> list[dict]:
    path = CANDIDATES_DIR / f"{today}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def filter_by_recency(candidates: list[dict], hours: int = 24) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = []
    for c in candidates:
        pub = c.get("pub_date")
        if pub:
            try:
                dt = datetime.fromisoformat(pub)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    result.append(c)
            except ValueError:
                result.append(c)  # unparseable date → include rather than drop
        else:
            result.append(c)  # HTML sources have no pub_date — always include
    return result


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def call_gemini(client: genai.Client, prompt: str) -> str:
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return response.text


def select_to_scrape(candidates: list[dict], client: genai.Client) -> list[dict]:
    slim = [{"title": c["title"], "url": c["url"], "source": c["source"]} for c in candidates]
    prompt = SELECT_PROMPT.format(candidates_json=json.dumps(slim, indent=2), top_n=SCRAPE_TOP_N)
    return json.loads(strip_fences(call_gemini(client, prompt)))


async def scrape_one(crawler, run_config, item: dict) -> dict:
    try:
        result = await crawler.arun(url=item["url"], config=run_config)
        if result.success:
            md = result.markdown
            text = (md.fit_markdown or md.raw_markdown) if hasattr(md, "fit_markdown") else (md or "")
            return {**item, "content": text[:8000]}
        return {**item, "content": None}
    except Exception:
        return {**item, "content": None}


async def scrape_articles(items: list[dict]) -> list[dict]:
    browser_config = BrowserConfig(headless=True, enable_stealth=True)
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, scan_full_page=True)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await asyncio.gather(*[scrape_one(crawler, run_config, item) for item in items])
    return list(results)


def build_digest(scraped: list[dict], client: genai.Client) -> dict:
    articles_text = ""
    for i, item in enumerate(scraped, 1):
        articles_text += f"\n---\nARTICLE {i}: {item['title']}\nURL: {item['url']}\nSOURCE: {item.get('source', '')}\n"
        articles_text += item["content"] if item.get("content") else "[Content could not be retrieved]"
        articles_text += "\n"
    prompt = DIGEST_PROMPT.format(articles_text=articles_text)
    return json.loads(strip_fences(call_gemini(client, prompt)))


SEP = '<p style="color:#ccc;margin:14px 0;letter-spacing:1px;font-size:13px;">━━━━━━━━━━━━━━━━━━</p>'


def render_top_stories(items: list[dict]) -> str:
    if not items:
        return ""
    parts = []
    for s in items:
        link = f'<a href="{s["url"]}" style="color:#0055cc;text-decoration:none;">{s["title"]}</a>'
        parts.append(
            f'<div style="margin-bottom:12px;">'
            f'<p style="margin:0;">• {link} <span style="color:#999;font-size:11px;">({s.get("source","")})</span></p>'
            f'<p style="margin:2px 0 1px 14px;color:#333;">→ {s["what"]}</p>'
            f'<p style="margin:1px 0 0 14px;color:#666;">→ {s["why"]}</p>'
            f'</div>'
        )
    return "\n".join(parts)


def render_bullets(items: list[dict]) -> str:
    if not items:
        return ""
    parts = []
    for s in items:
        link = f'<a href="{s["url"]}" style="color:#0055cc;text-decoration:none;">{s["title"]}</a>'
        parts.append(
            f'<p style="margin:4px 0;">• {link} '
            f'<span style="color:#999;font-size:11px;">({s.get("source","")})</span>'
            f' — {s["what"]}</p>'
        )
    return "\n".join(parts)


def section(emoji: str, title: str, content: str) -> str:
    if not content:
        return ""
    return (
        f'{SEP}'
        f'<p style="margin:12px 0 8px;font-weight:bold;">{emoji} {title}</p>'
        f'{content}'
    )


def build_email_html(digest: dict, total_candidates: int, recent_count: int, today: str) -> str:
    tldr_html = "\n".join(f'<p style="margin:3px 0;">• {b}</p>' for b in digest.get("tldr", []))

    return f"""<html><body style="font-family:monospace;max-width:620px;margin:0 auto;padding:20px 20px 32px;color:#222;line-height:1.6;font-size:13px;">
  <h2 style="margin-bottom:2px;font-size:15px;letter-spacing:0.5px;">AI Digest — {today} ET</h2>
  <p style="color:#aaa;font-size:11px;margin:0 0 4px;">{recent_count} articles from last 24h &nbsp;·&nbsp; filtered from {total_candidates} candidates</p>
  {SEP}
  <p style="margin:12px 0 8px;font-weight:bold;">🧠 TL;DR</p>
  {tldr_html}
  {section("🔥", "Top Stories", render_top_stories(digest.get("top_stories", [])))}
  {section("⚡", "Important Developments", render_bullets(digest.get("important_developments", [])))}
  {section("🛠️", "Builder Tools / Open Source", render_bullets(digest.get("builder_tools", [])))}
  {section("📊", "Market Signals", render_bullets(digest.get("market_signals", [])))}
  {SEP}
  <p style="color:#ccc;font-size:11px;margin-top:4px;">caio-blog-gather &nbsp;·&nbsp; automated daily digest</p>
</body></html>"""


def send_email(subject: str, html: str, app_password: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_FROM, app_password)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())


async def main():
    api_key = os.environ["GEMINI_API_KEY"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    today = date.today().isoformat()

    candidates = load_candidates(today)
    if not candidates:
        print(f"No candidates for {today}, skipping digest.")
        return

    client = genai.Client(api_key=api_key)

    recent = filter_by_recency(candidates)
    print(f"{len(recent)}/{len(candidates)} candidates pass 24h filter.")
    if not recent:
        print("No recent candidates, skipping digest.")
        return

    print(f"Selecting top {SCRAPE_TOP_N} to scrape (LLM call 1)...")
    selected = select_to_scrape(recent, client)
    print(f"Scraping {len(selected)} articles...")

    scraped = await scrape_articles(selected)

    print("Building digest (LLM call 2)...")
    digest = build_digest(scraped, client)

    html = build_email_html(digest, len(candidates), len(recent), today)
    send_email(f"AI Digest — {today}", html, app_password)
    print(f"Done. Email sent for {today}.")


if __name__ == "__main__":
    asyncio.run(main())
