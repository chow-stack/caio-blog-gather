"""
sunday_blog.py — Full Sunday blog build pipeline. 3 Gemini calls total.

  1. Prepare  — merge this week's candidate JSON files
  2. Filter   — Gemini selects the best 10-20 articles          (LLM call 1)
  3. Scrape   — crawl4ai fetches full text, writes Blog/raw/
  4. Process  — Gemini processes all sources in one call        (LLM call 2)
               → writes Blog/wiki/sources/
  5. Index    — appends week block to Blog/wiki/index.md
  6. Draft    — Gemini writes the outline scaffold              (LLM call 3)
               → writes Blog/drafts/YYYY-MM-DD-draft.md
  7. Log      — appends to Blog/log.md

Run from caio-os-scripts/:
    python sunday_blog.py
or with the venv:
    .venv/bin/python sunday_blog.py
"""

import asyncio
import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from google import genai
from google.genai import errors as genai_errors, types

from sources import VAULT_PATH

# ── Paths ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
VAULT = SCRIPT_DIR / VAULT_PATH
CANDIDATES_DIR = SCRIPT_DIR / "candidates"
TEMPLATE_FILE = VAULT / "Archive" / "templates" / "source.md"

BLOG_RAW_DIR = VAULT / "Blog" / "raw"
BLOG_SOURCES_DIR = VAULT / "Blog" / "wiki" / "sources"
BLOG_DRAFTS_DIR = VAULT / "Blog" / "drafts"
BLOG_INDEX_FILE = VAULT / "Blog" / "wiki" / "index.md"
BLOG_LOG_FILE = VAULT / "Blog" / "log.md"

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# ── Prompts ────────────────────────────────────────────────────────────────────

FILTER_PROMPT = """\
You are selecting AI articles for Caio's weekly blog. He's a college student building with AI \
who writes a weekly roundup about specific AI things released this week — new models, features, \
tools, agent workflows, experiments. Not company news. The things themselves.

For each candidate, ask: does this change how a reader understands AI, can use AI, or knows \
where AI is going? If yes, keep it.

Keep:
- AI doing something new — a capability, product, behavior, or use case that didn't exist or \
  wasn't obvious before
- How AI models actually work, fail, or surprise — training dynamics, design tradeoffs, \
  unexpected outputs, real incidents with specific consequences
- Anything that changes how you build with AI — tools, techniques, architectures, prompting \
  approaches, agent patterns, practical guides; applicable today or worth learning for later
- Moves by major players that signal the direction AI is heading — what the move means, \
  not the legal or financial mechanics
- AI appearing in everyday products and apps people actually use
- Something genuinely surprising or non-obvious with real depth

Remove:
- Duplicate coverage of the same story — keep the best source only
- Financial performance, revenue, stock reactions, user count milestones
- Deal terms, contract clauses, legal specifics
- Outages and status updates
- Articles with no real substance — one-sentence posts, link-only, minor version bumps
- Technical implementation walkthroughs with no accessible punchline
- "Company plans to..." with nothing to try yet
- Research paper with no usable demo or implementation
- Enterprise-only, waitlisted, or not publicly accessible
- Social or political controversy without a clear insight about the technology itself
- Geographic adoption stats or engagement metrics with no new capability shown

Candidates:
{candidates_json}

Return 10-20 of the best as JSON only — no markdown, no explanation:
[{{"title": "...", "url": "...", "source": "...", "date_found": "YYYY-MM-DD"}}]"""

PROCESS_PROMPT = """\
You are processing scraped articles into structured source pages for Caio's AI blog.
Caio is a college student building with AI writing a weekly roundup.

You will receive {n} articles. Return a JSON array with exactly {n} objects — one per article, \
in the same order. Do not drop or merge any article.

Articles:
{articles_text}

Return JSON only — no markdown, no explanation:
[
  {{
    "title": "...",
    "url": "...",
    "source": "...",
    "angle": "One sentence: what happened and why it matters for a college student building with AI",
    "key_points": [
      "Concrete detail worth keeping — specific fact, number, or behavior from the article",
      "Another concrete detail"
    ],
    "possible_blog_use": "One sentence on how this could appear in the post — lead item, supporting detail, closing thought, etc."
  }}
]

Rules:
- angle: one sentence, specific, no fluff — what changed and why it matters for builders
- key_points: 2-4 bullets, concrete facts grounded in the article — do not invent details
- possible_blog_use: one sentence, practical placement suggestion
- url: must match the input URL exactly
- return valid JSON only"""

DRAFT_PROMPT = """\
You are writing a draft outline scaffold for Caio's weekly AI blog post.
Caio writes entirely by hand — this scaffold gives him structure to write from. No prose.

Sources processed this week:
{sources_text}

Week: {monday} / {sunday}
Today: {today}

Write the following scaffold as plain markdown. Fill in every section with content specific \
to this week's sources — no placeholders, no brackets in the output:

# Weekly AI Roundup — week of {monday} / {sunday}
Generated: {today}

## Title options
- [option 1]
- [option 2]
- [option 3]

## Intro hook
Open by mentioning that [specific surprising thing from this week], then zoom out to \
"here's everything else that dropped this week."

## The items (in order)
### [Article title]
- The one-liner: [what happened in plain English]
- Your angle: [why a college student building with AI would care]

[one block per source, ordered most to least impactful]

## Closing thought
[One or two sentences on the real thread connecting this week's items — not obvious, not generic]

Rules:
- No prose — scaffold only
- Title options: specific to this week, not generic ("weekly roundup" is not a title)
- Items in order from most to least impactful/interesting
- Closing thought: name a real pattern you noticed across multiple items this week
- Fill everything in — no unfilled brackets in the final output"""

# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(title: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return re.sub(r"-+", "-", slug)[:max_len].rstrip("-")


def yaml_str(value: str) -> str:
    if any(c in value for c in (':', "'", '"', '#', '[')):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def week_bounds() -> tuple[str, str]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json|markdown)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ── Gemini ─────────────────────────────────────────────────────────────────────

def call_gemini(client: genai.Client, prompt: str, *, json_mode: bool = False) -> tuple[str, str]:
    """Returns (response_text, model_used). Retries on 429/503, falls back to lite model."""
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
                    wait = 2 ** (attempt + 1)
                    print(f"  {e.code} on {model} (attempt {attempt + 1}/3), retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        print(f"  {model} failed after 3 attempts, trying fallback...")
    raise RuntimeError("All models unavailable after retries — try again later.")


# ── Step 1: Prepare ────────────────────────────────────────────────────────────

def prepare() -> tuple[list[dict], list[Path]]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    seen, items, files = set(), [], []

    for i in range((today - monday).days + 1):
        f = CANDIDATES_DIR / f"{(monday + timedelta(days=i)).isoformat()}.json"
        if f.exists():
            files.append(f)

    if not files:
        return [], []

    for f in files:
        for item in json.loads(f.read_text(encoding="utf-8")):
            if item["url"] not in seen:
                seen.add(item["url"])
                items.append(item)

    print(f"  {len(items)} candidates from {len(files)} day(s).")
    return items, files


# ── Step 2: Filter ─────────────────────────────────────────────────────────────

def filter_candidates(candidates: list[dict], client: genai.Client) -> tuple[list[dict], str]:
    slim = [
        {"title": c["title"], "url": c["url"], "source": c["source"], "date_found": c["date_found"]}
        for c in candidates
    ]
    prompt = FILTER_PROMPT.format(candidates_json=json.dumps(slim, indent=2))
    text, model = call_gemini(client, prompt, json_mode=True)
    selected = json.loads(strip_fences(text))

    # Restore date_found from originals — don't trust the LLM to preserve it
    url_to_original = {c["url"]: c for c in candidates}
    for item in selected:
        if item["url"] in url_to_original:
            item["date_found"] = url_to_original[item["url"]]["date_found"]

    print(f"  {len(selected)} articles selected.")
    return selected, model


# ── Step 3: Scrape ─────────────────────────────────────────────────────────────

async def _scrape_one(
    crawler: AsyncWebCrawler,
    run_config: CrawlerRunConfig,
    item: dict,
    template: str,
) -> dict:
    slug = slugify(item["title"])
    out_path = BLOG_RAW_DIR / f"{item['date_found']}-{slug}.md"
    try:
        result = await crawler.arun(url=item["url"], config=run_config)
        if not result.success:
            raise RuntimeError("crawl returned success=False")
        md = result.markdown
        full_text = (md.fit_markdown or md.raw_markdown) if hasattr(md, "fit_markdown") else (md or "")

        # Write verbatim raw file
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
        BLOG_RAW_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(filled, encoding="utf-8")

        return {**item, "slug": slug, "content": full_text[:12000], "error": None}
    except Exception as e:
        return {**item, "slug": slug, "content": None, "error": str(e)[:120]}


async def scrape_all(items: list[dict]) -> list[dict]:
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    browser_config = BrowserConfig(headless=True, enable_stealth=True)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed")
        ),
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await asyncio.gather(
            *[_scrape_one(crawler, run_config, item, template) for item in items]
        )

    ok = [r for r in results if not r["error"]]
    errors = [r for r in results if r["error"]]
    print(f"  {len(ok)} scraped, {len(errors)} failed.")
    for r in errors:
        print(f"    SCRAPE ERROR: {r['title'][:60]}: {r['error']}")
    return list(results)


# ── Step 4: Process sources ────────────────────────────────────────────────────

def process_sources(scraped: list[dict], client: genai.Client) -> tuple[list[dict], str]:
    url_to_item = {s["url"]: s for s in scraped}

    articles_text = ""
    for i, item in enumerate(scraped, 1):
        articles_text += (
            f"\n---\nARTICLE {i}\n"
            f"Title: {item['title']}\n"
            f"URL: {item['url']}\n"
            f"Source: {item['source']}\n"
            f"Date: {item['date_found']}\n\n"
        )
        articles_text += item["content"] if item.get("content") else "[Content could not be retrieved]"
        articles_text += "\n"

    prompt = PROCESS_PROMPT.format(n=len(scraped), articles_text=articles_text)
    text, model = call_gemini(client, prompt, json_mode=True)
    raw_processed = json.loads(strip_fences(text))

    BLOG_SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    enriched = []
    for p in raw_processed:
        original = url_to_item.get(p.get("url", ""))
        slug = original["slug"] if original else slugify(p["title"])
        date_str = original["date_found"] if original else date.today().isoformat()
        key_points = "\n".join(f"- {kp}" for kp in p.get("key_points", []))

        content = (
            f"---\n"
            f"title: {yaml_str(p['title'])}\n"
            f"url: {p['url']}\n"
            f"date: {date_str}\n"
            f"source: {p['source']}\n"
            f"tags: [ai, blog]\n"
            f"---\n\n"
            f"## The angle\n{p['angle']}\n\n"
            f"## Key points\n{key_points}\n\n"
            f"## Possible blog use\n{p['possible_blog_use']}\n"
        )
        (BLOG_SOURCES_DIR / f"{date_str}-{slug}.md").write_text(content, encoding="utf-8")
        enriched.append({**p, "slug": slug, "date_found": date_str})

    print(f"  {len(enriched)} source pages written to Blog/wiki/sources/.")
    return enriched, model


# ── Step 5: Update index ───────────────────────────────────────────────────────

def update_index(processed: list[dict]):
    monday, sunday = week_bounds()
    rows = "\n".join(
        f"| {p['date_found']} "
        f"| [[Blog/wiki/sources/{p['date_found']}-{p['slug']}\\|{p['title']}]] |"
        for p in processed
    )
    block = f"\n## Week {monday} / {sunday} — {len(processed)} sources\n| Date | Title |\n|---|---|\n{rows}\n"

    existing = BLOG_INDEX_FILE.read_text(encoding="utf-8") if BLOG_INDEX_FILE.exists() else "# Blog Index"
    BLOG_INDEX_FILE.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
    print(f"  Blog/wiki/index.md updated.")


# ── Step 6: Draft outline ──────────────────────────────────────────────────────

def write_draft(processed: list[dict], client: genai.Client) -> tuple[str, str]:
    monday, sunday = week_bounds()
    today_str = date.today().isoformat()

    sources_text = ""
    for p in processed:
        key_points = "\n".join(f"- {kp}" for kp in p.get("key_points", []))
        sources_text += (
            f"\n### {p['title']}\n"
            f"URL: {p['url']}\n"
            f"Angle: {p['angle']}\n"
            f"Key points:\n{key_points}\n"
            f"Possible blog use: {p['possible_blog_use']}\n"
        )

    prompt = DRAFT_PROMPT.format(
        sources_text=sources_text,
        monday=monday,
        sunday=sunday,
        today=today_str,
    )
    draft_text, model = call_gemini(client, prompt)  # plain markdown, no json_mode

    BLOG_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    draft_file = BLOG_DRAFTS_DIR / f"{today_str}-draft.md"
    draft_file.write_text(strip_fences(draft_text), encoding="utf-8")
    print(f"  Draft written to Blog/drafts/{today_str}-draft.md.")
    return f"{today_str}-draft", model


# ── Step 7: Log ────────────────────────────────────────────────────────────────

def write_log(processed: list[dict], draft_name: str):
    today_str = date.today().isoformat()
    slugs = ", ".join(
        f"[[{p['date_found']}-{p['slug']}]]" for p in processed
    )
    entry = (
        f"\n## {today_str} | sunday-build | {len(processed)} sources processed\n"
        f"- Source pages: {slugs}\n"
        f"- Draft: [[Blog/drafts/{draft_name}]]\n"
    )
    BLOG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with BLOG_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(entry)
    print(f"  Blog/log.md updated.")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    load_dotenv(SCRIPT_DIR / ".env")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: GEMINI_API_KEY not set.")

    client = genai.Client(api_key=api_key)

    print("Step 1 — Prepare")
    candidates, candidate_files = prepare()
    if not candidates:
        print("No candidate files found for this week. Did gather.py run?")
        return

    print("\nStep 2 — Filter  [LLM call 1/3]")
    selected, _ = filter_candidates(candidates, client)
    if not selected:
        print("No articles passed the filter. Exiting.")
        return

    print("\nStep 3 — Scrape")
    scraped = await scrape_all(selected)
    scraped_ok = [s for s in scraped if s.get("content")]
    if not scraped_ok:
        print("All scrapes failed. Exiting.")
        return

    print("\nStep 4 — Process sources  [LLM call 2/3]")
    processed, _ = process_sources(scraped_ok, client)

    print("\nStep 5 — Update index")
    update_index(processed)

    print("\nStep 6 — Draft outline  [LLM call 3/3]")
    draft_name, _ = write_draft(processed, client)

    print("\nStep 7 — Log")
    write_log(processed, draft_name)

    for f in candidate_files:
        f.unlink(missing_ok=True)
    (CANDIDATES_DIR / "this_week.json").unlink(missing_ok=True)

    print(f"\n✓ Done. {len(processed)} sources processed → Blog/drafts/{draft_name}.md")


if __name__ == "__main__":
    asyncio.run(main())
