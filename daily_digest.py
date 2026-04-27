import asyncio
import json
import os
import re
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import google.generativeai as genai
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

SCRIPT_DIR = Path(__file__).parent
CANDIDATES_DIR = SCRIPT_DIR / "candidates"

EMAIL_FROM = "cdesouza202@gmail.com"
EMAIL_TO = "cdesouza202@gmail.com"
TOP_N = 10

FILTER_PROMPT = """\
You are filtering AI news for Caio, a college student who writes a weekly blog about practical AI.

He cares about: new model releases, new tool features (Claude, ChatGPT, Cursor, Gemini, etc.),
agent workflows, prompt techniques, open-source releases runnable on consumer hardware,
surprising or non-obvious developments.

He does NOT care about: funding rounds, acquisitions, company valuations, vague research with
no demo, enterprise-only tools, generic opinion pieces.

Today's candidates:
{candidates_json}

Return the {top_n} most relevant as JSON only — no markdown, no explanation:
[{{"title": "...", "url": "...", "source": "...", "date_found": "...", "reason": "one line why this matters"}}]"""

SUMMARY_PROMPT = """\
Summarize these AI news articles for Caio, a college student building with AI who writes a weekly blog.

For each article return a JSON array with this structure:
[{{
  "title": "...",
  "url": "...",
  "source": "...",
  "what": "one sentence — what actually happened in plain English",
  "why": "one sentence — why this matters to someone building with AI",
  "blog": true or false,
  "blog_angle": "one-line angle if blog is true, else null"
}}]

If an article's content is marked [Content could not be retrieved], summarize from the title alone
and set blog to false.

Return JSON only — no markdown, no explanation.

Articles:
{articles_text}"""


def load_candidates(today: str) -> list[dict]:
    path = CANDIDATES_DIR / f"{today}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def parse_json_response(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


def filter_candidates(candidates: list[dict], api_key: str) -> list[dict]:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    slim = [
        {"title": c["title"], "url": c["url"], "source": c["source"], "date_found": c["date_found"]}
        for c in candidates
    ]
    prompt = FILTER_PROMPT.format(candidates_json=json.dumps(slim, indent=2), top_n=TOP_N)
    response = model.generate_content(prompt)
    return parse_json_response(response.text)


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


def summarize_articles(items: list[dict], api_key: str) -> list[dict]:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    articles_text = ""
    for i, item in enumerate(items, 1):
        articles_text += f"\n---\nARTICLE {i}: {item['title']}\nURL: {item['url']}\n"
        articles_text += item["content"] if item.get("content") else "[Content could not be retrieved]"
        articles_text += "\n"
    prompt = SUMMARY_PROMPT.format(articles_text=articles_text)
    response = model.generate_content(prompt)
    return parse_json_response(response.text)


def build_email_html(summaries: list[dict], total_candidates: int, today: str) -> str:
    items_html = ""
    for s in summaries:
        blog_html = ""
        if s.get("blog") and s.get("blog_angle"):
            blog_html = f'<p style="margin:4px 0;color:#1a7f4b;font-size:12px;">Blog angle: {s["blog_angle"]}</p>'
        items_html += f"""
        <div style="margin-bottom:24px;border-left:3px solid #0066cc;padding-left:14px;">
          <p style="margin:0 0 4px 0;font-size:15px;">
            <strong><a href="{s['url']}" style="color:#0066cc;text-decoration:none;">{s['title']}</a></strong>
            <span style="color:#999;font-size:11px;margin-left:6px;">{s.get('source', '')}</span>
          </p>
          <p style="margin:4px 0;font-size:13px;">{s['what']}</p>
          <p style="margin:4px 0;font-size:13px;color:#555;">{s['why']}</p>
          {blog_html}
        </div>"""

    return f"""<html><body style="font-family:monospace;max-width:620px;margin:0 auto;padding:20px;color:#222;line-height:1.5;">
  <h2 style="margin-bottom:4px;">AI Digest — {today}</h2>
  <p style="color:#999;font-size:12px;margin-top:0;">{len(summaries)} articles &middot; filtered from {total_candidates} candidates</p>
  {items_html}
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
  <p style="color:#bbb;font-size:11px;">caio-blog-gather &middot; automated daily digest</p>
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

    print(f"Filtering {len(candidates)} candidates...")
    filtered = filter_candidates(candidates, api_key)
    print(f"Selected {len(filtered)}. Scraping full content...")

    scraped = await scrape_articles(filtered)

    print("Summarizing...")
    summaries = summarize_articles(scraped, api_key)

    html = build_email_html(summaries, len(candidates), today)
    send_email(f"AI Digest — {today}", html, app_password)
    print(f"Done. Email sent for {today}.")


if __name__ == "__main__":
    asyncio.run(main())
