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
SMOL_SOURCE = "news.smol.ai"
MODEL = "gemini-2.5-flash"

# Call 1: titles only → select what's worth scraping
SELECT_PROMPT = """\
You are selecting AI news articles to read in full for Caio, a college student who builds with AI \
and writes a weekly blog.

He cares about: new model releases, tool features (Claude, ChatGPT, Cursor, Gemini, etc.), \
agent workflows, prompt techniques, open-source releases runnable on consumer hardware, \
surprising, unique or non-obvious AI developments.

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
  "subject_line": "6-10 words capturing the day's most important story — specific, no clickbait",
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
- subject_line: lead with the biggest story ("GPT-5 released, Anthropic enters enterprise" not "Big day for AI")
- tldr: 3-4 bullets on the day's themes — specific and high-signal
- top_stories: max 5, most impactful/actionable — must have both what + why
- important_developments: notable but not top-tier
- builder_tools: tools, APIs, open-source, prompt techniques usable today
- market_signals: company moves worth tracking — keep minimal, skip pure funding/valuation news
- news.smol.ai articles are the most curated — always prioritize them for top_stories when relevant
- every article goes in exactly one category, or is dropped if not worth including
- shorten and clean titles — remove clickbait, trim length
- return JSON only, no markdown, no explanation

Articles:
{articles_text}"""

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


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
            result.append(c)  # no pub_date → always include
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
    # smol.ai articles are always included — they're the most curated source
    smol = [c for c in candidates if c["source"] == SMOL_SOURCE]
    others = [c for c in candidates if c["source"] != SMOL_SOURCE]

    remaining = SCRAPE_TOP_N - len(smol)
    if remaining <= 0 or not others:
        return smol[:SCRAPE_TOP_N]

    slim = [{"title": c["title"], "url": c["url"], "source": c["source"]} for c in others]
    prompt = SELECT_PROMPT.format(candidates_json=json.dumps(slim, indent=2), top_n=remaining)
    llm_selected = json.loads(strip_fences(call_gemini(client, prompt)))
    return smol + llm_selected


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


def a(href: str, text: str) -> str:
    return f'<a href="{href}" style="color:#111;text-decoration:underline;text-underline-offset:2px;">{text}</a>'


def render_top_stories(items: list[dict]) -> str:
    if not items:
        return ""
    parts = []
    for s in items:
        src = f'<span style="color:#888;font-size:12px;font-weight:400;margin-left:6px;">{s.get("source","")}</span>'
        parts.append(
            f'<div style="margin-bottom:20px;">'
            f'<p style="margin:0 0 3px;font-size:14px;font-weight:600;line-height:1.4;">{a(s["url"], s["title"])}{src}</p>'
            f'<p style="margin:0 0 2px;font-size:13px;color:#333;line-height:1.5;">{s["what"]}</p>'
            f'<p style="margin:0;font-size:13px;color:#555;font-style:italic;line-height:1.5;">{s["why"]}</p>'
            f'</div>'
        )
    return "\n".join(parts)


def render_bullets(items: list[dict]) -> str:
    if not items:
        return ""
    parts = []
    for s in items:
        src = f'<span style="color:#888;font-size:12px;"> ({s.get("source","")})</span>'
        parts.append(
            f'<p style="margin:0 0 10px;font-size:13px;line-height:1.5;">'
            f'<span style="font-weight:600;">{a(s["url"], s["title"])}</span>{src}'
            f' &mdash; {s["what"]}</p>'
        )
    return "\n".join(parts)


def section_label(title: str) -> str:
    return (
        f'<p style="margin:0 0 14px;font-size:11px;font-weight:600;letter-spacing:0.08em;'
        f'text-transform:uppercase;color:#888;">{title}</p>'
    )


def hr() -> str:
    return '<hr style="border:none;border-top:1px solid #e8e8e8;margin:24px 0;">'


def build_email_html(digest: dict, total_candidates: int, recent_count: int, today_label: str) -> str:
    tldr_items = "".join(
        f'<li style="margin-bottom:6px;font-size:14px;line-height:1.5;">{b}</li>'
        for b in digest.get("tldr", [])
    )

    top = render_top_stories(digest.get("top_stories", []))
    devs = render_bullets(digest.get("important_developments", []))
    tools = render_bullets(digest.get("builder_tools", []))
    market = render_bullets(digest.get("market_signals", []))

    def section(label: str, content: str) -> str:
        if not content:
            return ""
        return f'{hr()}{section_label(label)}{content}'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:{FONT};max-width:600px;margin:0 auto;padding:32px 24px 48px;color:#111;background:#fff;-webkit-font-smoothing:antialiased;">

  <div style="margin-bottom:28px;padding-bottom:20px;border-bottom:2px solid #111;">
    <h1 style="font-size:22px;font-weight:700;margin:0 0 6px;letter-spacing:-0.3px;">AI Daily</h1>
    <p style="margin:0;font-size:13px;color:#888;">{today_label} &nbsp;&middot;&nbsp; {recent_count} articles &nbsp;&middot;&nbsp; {total_candidates} candidates</p>
  </div>

  {section_label("Today at a glance")}
  <ul style="margin:0 0 0 4px;padding:0 0 0 16px;list-style:disc;">
    {tldr_items}
  </ul>

  {section("Top Stories", top)}
  {section("Important Developments", devs)}
  {section("Builder Tools &amp; Open Source", tools)}
  {section("Market Signals", market)}

  {hr()}
  <p style="margin:0;font-size:11px;color:#bbb;">AI Daily &nbsp;&middot;&nbsp; caio-blog-gather &nbsp;&middot;&nbsp; automated</p>

</body>
</html>"""


def format_subject(digest: dict, today_label: str) -> str:
    custom = digest.get("subject_line", "").strip()
    if custom:
        return f"{custom} — {today_label}"
    return f"AI Daily — {today_label}"


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
    now = datetime.now(timezone.utc)
    today = date.today().isoformat()
    today_label = now.strftime("%A, %B %-d")  # e.g. "Monday, April 28"

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

    subject = format_subject(digest, today_label)
    html = build_email_html(digest, len(candidates), len(recent), today_label)
    send_email(subject, html, app_password)
    print(f"Done. Sent: {subject}")


if __name__ == "__main__":
    asyncio.run(main())
