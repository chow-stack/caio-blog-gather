import asyncio
import json
import os
import re
import smtplib
import time
from datetime import date, datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google import genai
from google.genai import errors as genai_errors, types
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

SCRIPT_DIR = Path(__file__).parent
CANDIDATES_DIR = SCRIPT_DIR / "candidates"

EMAIL_FROM = "cdesouza202@gmail.com"
EMAIL_TO = "cdesouza202@gmail.com"
SCRAPE_TOP_N = 15
MODELS = [
    "gemini-2.5-flash",       # primary — best price-performance, stable GA
    "gemini-2.5-flash-lite",  # fallback — cheaper, higher quota headroom
]
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

ALL_SECTIONS = ["top_stories", "important_developments", "builder_tools", "market_signals", "other_notable"]

# Call 1: titles only → select what's worth scraping
SELECT_PROMPT = """\
You are selecting AI news articles to read in full for Caio, a college student who builds with AI.

For each article, ask: does this change how someone understands AI, can use AI, or knows where AI \
is going? If yes, keep it. If it just measures, counts, negotiates, or patches something without \
adding insight, skip it.

Keep:
- AI doing something new — a capability, product, behavior, or use case that didn't exist or \
wasn't obvious before (new models, agent workflows, AI in unexpected industries or products)
- How AI models actually work, fail, or surprise — training dynamics, design tradeoffs, \
unexpected outputs, real incidents with specific consequences, research with an accessible insight
- Anything that changes how you build with AI — tools, techniques, architectures, prompting \
approaches, agent patterns, practical guides; applicable today or worth understanding for later
- Moves by major players that signal the direction AI is heading — what the move means, \
not the legal mechanics of it
- AI appearing in everyday products and apps people actually use (phones, cars, YouTube, Amazon, \
creative software, etc.)

Skip:
- Financial performance, revenue targets, stock reactions, user count milestones — measuring \
without insight
- Deal terms, contract clauses, legal specifics — business mechanics with no directional signal
- Outages and status updates
- Articles with no real substance — one-sentence posts, link-only pages, minor version bumps, \
patch notes
- Deep technical implementation walkthroughs with no accessible punchline or new takeaway
- Announcements of things that don't exist yet with nothing to try
- Social or political controversy without a clear insight about the technology itself
- Geographic adoption stats or engagement metrics with no new capability shown
- Duplicate coverage of the same story — include only the best source, drop the rest

Candidates:
{candidates_json}

Return the {top_n} most worth reading in full as JSON only — no markdown:
[{{"title": "...", "url": "...", "source": "..."}}]"""

# Call 2: full content → complete structured digest
DIGEST_PROMPT = """\
You are building a daily AI digest for Caio, a college student who builds with AI and writes a weekly blog.

You are given {n_articles} articles. You must include every article exactly once in the final digest. \
Do not drop, merge, summarize away, or ignore any article. \
Top Stories may contain a maximum of 5 articles, but all remaining articles must be placed into \
Important Developments, Builder Tools & Open Source, Market Signals, or Other Notable Items.

If multiple articles cover the same story or release, place the best one in the appropriate section \
and consolidate the others under it — do not give each its own entry.

Return a single JSON object with this exact structure:
{{
  "subject_line": "6-10 words capturing the day's biggest story — specific, no clickbait",
  "tldr": ["bullet 1", "bullet 2", "bullet 3"],
  "top_stories": [
    {{"title": "short clean title", "url": "...", "source": "...", \
"what": "1 sentence — what happened and why it matters to builders", \
"bullets": ["2-3 short bullets: specific data points, key causes, or actionable insights \
beyond the what — e.g. exact numbers, what triggered the event, how to apply it"]}}
  ],
  "important_developments": [
    {{"title": "short clean title", "url": "...", "source": "...", \
"what": "1 sentence — what happened and why it matters", \
"bullets": ["1-2 short bullets with a specific detail, number, or insight from the article — \
omit if the article has no depth beyond the headline"]}}
  ],
  "builder_tools": [
    {{"title": "short clean title", "url": "...", "source": "...", \
"what": "1 sentence — what happened and why it matters", \
"bullets": ["1-2 short bullets with a specific detail, number, or insight from the article — \
omit if the article has no depth beyond the headline"]}}
  ],
  "market_signals": [
    {{"title": "short clean title", "url": "...", "source": "...", \
"what": "1 sentence — what happened and why it matters", \
"bullets": ["1-2 short bullets with a specific detail, number, or insight from the article — \
omit if the article has no depth beyond the headline"]}}
  ],
  "other_notable": [
    {{"title": "short clean title", "url": "...", "source": "...", \
"what": "1 sentence — what happened and why it matters"}}
  ]
}}

Section rules:
- top_stories: max 5, the most impactful/actionable articles — must have what and bullets
- important_developments: product updates, model news, company moves not in top stories
- builder_tools: tools, APIs, open-source projects, prompt techniques usable today
- market_signals: funding, enterprise strategy, policy, major business moves
- other_notable: anything that does not fit cleanly elsewhere — use this rather than dropping articles; \
  bullets optional here since these are low-priority
- shorten and clean titles — remove clickbait, no truncation with "..."
- bullets must be grounded in actual article content — do not invent or speculate
- return JSON only, no markdown, no explanation

Articles:
{articles_text}"""


def load_candidates(today: str) -> list[dict]:
    path = CANDIDATES_DIR / f"{today}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def filter_by_recency(candidates: list[dict], hours: int = 48) -> list[dict]:
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
                result.append(c)
        else:
            result.append(c)
    return result


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def call_gemini(client: genai.Client, prompt: str, *, json_mode: bool = False) -> tuple[str, str]:
    """Returns (response_text, model_used)."""
    config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    for model in MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                if model != MODELS[0]:
                    print(f"  (used fallback model: {model})")
                return response.text, model
            except genai_errors.APIError as e:
                if e.code in (503, 429):
                    wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    print(f"  {e.code} on {model} (attempt {attempt + 1}/3), retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        print(f"  {model} failed after 3 attempts, trying fallback...")
    raise RuntimeError("All models unavailable after retries — try again later.")


def select_to_scrape(candidates: list[dict], client: genai.Client) -> tuple[list[dict], str | None]:
    """Returns (selected_articles, model_used). model_used is None if no LLM call was made."""
    if not candidates:
        return [], None
    slim = [{"title": c["title"], "url": c["url"], "source": c["source"]} for c in candidates]
    prompt = SELECT_PROMPT.format(candidates_json=json.dumps(slim, indent=2), top_n=SCRAPE_TOP_N)
    text, model_used = call_gemini(client, prompt, json_mode=True)
    return json.loads(strip_fences(text)), model_used


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


def build_digest(scraped: list[dict], client: genai.Client) -> tuple[dict, str]:
    """Returns (digest, model_used)."""
    articles_text = ""
    for i, item in enumerate(scraped, 1):
        articles_text += (
            f"\n---\nARTICLE {i}: {item['title']}\n"
            f"URL: {item['url']}\nSOURCE: {item.get('source', '')}\n"
        )
        articles_text += item["content"] if item.get("content") else "[Content could not be retrieved]"
        articles_text += "\n"
    prompt = DIGEST_PROMPT.format(n_articles=len(scraped), articles_text=articles_text)
    text, model_used = call_gemini(client, prompt, json_mode=True)
    return json.loads(strip_fences(text)), model_used


def validate_and_patch(digest: dict, scraped: list[dict]) -> tuple[dict, int]:
    """Ensure every scraped article appears in the digest. Returns patched digest and missing count."""
    present_urls = {
        item.get("url", "")
        for section in ALL_SECTIONS
        for item in digest.get(section, [])
    }

    missing = [s for s in scraped if s["url"] not in present_urls]
    if missing:
        if "other_notable" not in digest:
            digest["other_notable"] = []
        for s in missing:
            digest["other_notable"].append({
                "title": s["title"],
                "url": s["url"],
                "source": s.get("source", ""),
                "what": "See article for details.",
            })

    return digest, len(missing)


# ── HTML rendering ──────────────────────────────────────────────────────────

def a(href: str, text: str) -> str:
    return f'<a href="{href}" style="color:#111;text-decoration:underline;text-underline-offset:2px;">{text}</a>'


def src_tag(source: str) -> str:
    return f'<span style="color:#888;font-size:12px;font-weight:400;"> ({source})</span>'


def render_sub_bullets(bullets: list[str], indent: str = "18px") -> str:
    if not bullets:
        return ""
    items = "".join(
        f'<li style="margin-bottom:3px;font-size:12px;color:#444;line-height:1.5;">{b}</li>'
        for b in bullets
    )
    return (
        f'<ul style="margin:5px 0 0 {indent};padding:0 0 0 14px;list-style:disc;">'
        f'{items}</ul>'
    )


def render_top_stories(items: list[dict]) -> str:
    if not items:
        return ""
    parts = []
    for i, s in enumerate(items, 1):
        sub = render_sub_bullets(s.get("bullets", []), indent="16px")
        parts.append(
            f'<div style="margin-bottom:10px;">'
            f'<p style="margin:0;font-size:13px;line-height:1.5;">'
            f'<span style="font-weight:600;">{i}. {a(s["url"], s["title"])}</span>'
            f'{src_tag(s.get("source", ""))}'
            f' &mdash; {s["what"]}</p>'
            f'{sub}'
            f'</div>'
        )
    return "\n".join(parts)


def render_bullets(items: list[dict]) -> str:
    if not items:
        return ""
    parts = []
    for s in items:
        sub = render_sub_bullets(s.get("bullets", []), indent="16px")
        parts.append(
            f'<div style="margin-bottom:10px;">'
            f'<p style="margin:0;font-size:13px;line-height:1.5;">'
            f'&bull; <span style="font-weight:600;">{a(s["url"], s["title"])}</span>'
            f'{src_tag(s.get("source", ""))}'
            f' &mdash; {s["what"]}</p>'
            f'{sub}'
            f'</div>'
        )
    return "\n".join(parts)


def section_label(title: str) -> str:
    return (
        f'<p style="margin:0 0 14px;font-size:11px;font-weight:600;letter-spacing:0.08em;'
        f'text-transform:uppercase;color:#888;">{title}</p>'
    )


def hr() -> str:
    return '<hr style="border:none;border-top:1px solid #e8e8e8;margin:24px 0;">'


def section_block(label: str, content: str) -> str:
    if not content:
        return ""
    return f'{hr()}{section_label(label)}{content}'


def build_empty_email_html(today_label: str, reason: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:{FONT};max-width:600px;margin:0 auto;padding:32px 24px 48px;color:#111;background:#fff;-webkit-font-smoothing:antialiased;">

  <div style="margin-bottom:40px;padding-bottom:20px;border-bottom:2px solid #111;">
    <h1 style="font-size:22px;font-weight:700;margin:0 0 6px;letter-spacing:-0.3px;">AI Daily</h1>
    <p style="margin:0;font-size:13px;color:#888;">{today_label}</p>
  </div>

  <div style="padding:48px 0;text-align:center;">
    <p style="margin:0 0 12px;font-size:32px;font-weight:300;letter-spacing:-0.5px;color:#111;">Nothing new today.</p>
    <p style="margin:0;font-size:14px;color:#999;line-height:1.6;">{reason}<br>The system is running normally.</p>
  </div>

  <hr style="border:none;border-top:1px solid #e8e8e8;margin:40px 0 24px;">
  <p style="margin:0;font-size:11px;color:#bbb;">AI Daily &nbsp;&middot;&nbsp; caio-os-scripts &nbsp;&middot;&nbsp; automated</p>

</body>
</html>"""


def format_models_used(model_select: str | None, model_digest: str) -> str:
    primary = MODELS[0]
    parts = []
    if model_select and model_select != primary:
        parts.append(f"select: {model_select}")
    if model_digest != primary:
        parts.append(f"digest: {model_digest}")
    if parts:
        return f"{primary} + fallback ({', '.join(parts)})"
    return primary


def build_email_html(digest: dict, n_articles: int, total_candidates: int, today_label: str, model_select: str | None, model_digest: str) -> str:
    tldr_items = "".join(
        f'<li style="margin-bottom:6px;font-size:14px;line-height:1.5;">{b}</li>'
        for b in digest.get("tldr", [])
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:{FONT};max-width:600px;margin:0 auto;padding:32px 24px 48px;color:#111;background:#fff;-webkit-font-smoothing:antialiased;">

  <div style="margin-bottom:28px;padding-bottom:20px;border-bottom:2px solid #111;">
    <h1 style="font-size:22px;font-weight:700;margin:0 0 6px;letter-spacing:-0.3px;">AI Daily</h1>
    <p style="margin:0;font-size:13px;color:#888;">{today_label} &nbsp;&middot;&nbsp; {n_articles} articles &nbsp;&middot;&nbsp; {total_candidates} candidates</p>
  </div>

  {section_label("Today at a glance")}
  <ul style="margin:0 0 0 4px;padding:0 0 0 16px;list-style:disc;">
    {tldr_items}
  </ul>

  {section_block("Top Stories", render_top_stories(digest.get("top_stories", [])))}
  {section_block("Important Developments", render_bullets(digest.get("important_developments", [])))}
  {section_block("Builder Tools &amp; Open Source", render_bullets(digest.get("builder_tools", [])))}
  {section_block("Market Signals", render_bullets(digest.get("market_signals", [])))}
  {section_block("Other Notable Items", render_bullets(digest.get("other_notable", [])))}

  {hr()}
  <p style="margin:0;font-size:11px;color:#bbb;">AI Daily &nbsp;&middot;&nbsp; caio-os-scripts &nbsp;&middot;&nbsp; {format_models_used(model_select, model_digest)}</p>

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
    today_label = now.strftime("%A, %B %-d")

    candidates = load_candidates(today)
    if not candidates:
        print(f"No candidates for {today} — sending empty digest.")
        html = build_empty_email_html(today_label, "No articles were found across all sources.")
        send_email(f"AI Daily — {today_label}", html, app_password)
        return

    client = genai.Client(api_key=api_key)

    recent = filter_by_recency(candidates)
    print(f"{len(recent)}/{len(candidates)} candidates pass 24h filter.")
    if not recent:
        print("No recent candidates — sending empty digest.")
        html = build_empty_email_html(today_label, f"All {len(candidates)} candidate{'s' if len(candidates) != 1 else ''} found today were older than 24 hours.")
        send_email(f"AI Daily — {today_label}", html, app_password)
        return

    print(f"Selecting top {SCRAPE_TOP_N} to scrape (LLM call 1)...")
    selected, model_select = select_to_scrape(recent, client)
    print(f"Scraping {len(selected)} articles...")

    scraped = await scrape_articles(selected)

    print("Building digest (LLM call 2)...")
    digest, model_digest = build_digest(scraped, client)

    digest, n_missing = validate_and_patch(digest, scraped)
    if n_missing:
        print(f"Warning: LLM dropped {n_missing} article(s) — patched into Other Notable Items.")

    n_articles = sum(len(digest.get(s, [])) for s in ALL_SECTIONS)
    subject = format_subject(digest, today_label)
    html = build_email_html(digest, n_articles, len(candidates), today_label, model_select, model_digest)
    send_email(subject, html, app_password)
    print(f"Done. Sent: {subject} ({n_articles} articles in digest)")



if __name__ == "__main__":
    asyncio.run(main())
