from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai

from digest.config import load_config
from digest.constants import ALL_SECTIONS
from digest.email import article_count, format_subject, render_html, render_text, send_email
from digest.feeds import filter_by_recency, gather
from digest.generator import build_digest, scrape_articles, select_articles, validate_digest
from digest.state import load_seen, save_seen

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.toml"
STATE_PATH = ROOT / "state" / "seen.json"
OUTPUT_DIR = ROOT / "output"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def email_context(config: dict, digest: dict, candidates: list[dict], models_used: list[str], now: datetime) -> dict:
    email_config = config["email"]
    return {
        "digest_name": config["digest"]["name"],
        "tagline": config["digest"]["tagline"],
        "date_label": now.strftime("%A, %B %-d"),
        "preheader": (digest.get("tldr") or [digest.get("empty_reason", "Today's AI digest")])[0],
        "theme": config["theme"],
        "sections": config["sections"],
        "section_order": ALL_SECTIONS,
        "article_total": article_count(digest),
        "candidate_total": len(candidates),
        "show_candidate_count": email_config.get("show_candidate_count", False),
        "show_model": email_config.get("show_model", False),
        "models_used": list(dict.fromkeys(model for model in models_used if model)),
    }


def save_preview(config: dict, digest: dict, candidates: list[dict], models_used: list[str], now: datetime) -> tuple[Path, str, str]:
    context = email_context(config, digest, candidates, models_used, now)
    html = render_html(ROOT / "templates" / "email.html", digest, context)
    plain = render_text(digest, config["digest"]["name"], context["date_label"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    preview_path = OUTPUT_DIR / "preview.html"
    preview_path.write_text(html)
    return preview_path, html, plain


def sample_digest() -> dict:
    return {
        "subject_line": "Agents learn to work across your tools",
        "tldr": [
            "Agent workflows are becoming easier to compose and inspect.",
            "Smaller models continue to close the gap for everyday building tasks.",
            "Open-source tooling is making local experimentation more practical.",
        ],
        "top_stories": [
            {
                "title": "A better way to orchestrate AI agents",
                "url": "https://example.com/agents",
                "source": "Example Research",
                "what": "A new orchestration approach makes multi-step agent work easier to observe and recover when something fails.",
                "bullets": ["Each task records its inputs and outputs.", "Failed steps can resume without restarting the whole workflow."],
            },
            {
                "title": "Small models get more capable",
                "url": "https://example.com/models",
                "source": "Example AI",
                "what": "A compact model reaches stronger results while remaining inexpensive enough for frequent product use.",
                "bullets": ["The largest gains appear in coding and tool use."],
            },
        ],
        "important_developments": [],
        "builder_tools": [
            {
                "title": "A local toolkit for testing prompts",
                "url": "https://example.com/toolkit",
                "source": "Example GitHub",
                "what": "The toolkit compares prompts and model outputs from a single local dashboard.",
                "bullets": [],
            }
        ],
        "market_signals": [],
        "other_notable": [],
    }


async def run_pipeline(config: dict, dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    seen = load_seen(STATE_PATH, now)
    candidates, errors = gather(config["sources"], set(seen), now)
    write_json(OUTPUT_DIR / "candidates.json", candidates)
    print(f"Gathered {len(candidates)} candidates from {len(config['sources'])} sources")
    for error in errors:
        print(f"  Source error: {error}")

    hours = config["digest"]["recency_hours"]
    recent = filter_by_recency(candidates, hours, now)
    print(f"{len(recent)}/{len(candidates)} candidates pass the {hours}h filter")

    models = [config["models"]["primary"], *config["models"].get("fallbacks", [])]
    models_used: list[str] = []
    if recent:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required for a real run")
        client = genai.Client(api_key=api_key)
        selected, selection_model = select_articles(
            recent,
            client,
            ROOT / "prompts" / "select.md",
            models,
            config["digest"]["max_articles"],
        )
        models_used.append(selection_model or "")
        if selected:
            print(f"Scraping {len(selected)} selected articles")
            scraped = await scrape_articles(selected)
            digest, digest_model = build_digest(
                scraped,
                client,
                ROOT / "prompts" / "digest.md",
                models,
                config["digest"]["top_stories"],
            )
            models_used.append(digest_model)
            digest, missing = validate_digest(
                digest,
                scraped,
                config["digest"]["top_stories"],
            )
            if missing:
                print(f"Patched {missing} omitted article(s) into Other Notable")
        else:
            digest = {
                "subject_line": "",
                "tldr": [],
                "empty_reason": "No gathered articles passed the editorial selection.",
                **{section: [] for section in ALL_SECTIONS},
            }
    else:
        digest = {
            "subject_line": "",
            "tldr": [],
            "empty_reason": "No recent articles were found across the enabled sources.",
            **{section: [] for section in ALL_SECTIONS},
        }

    write_json(OUTPUT_DIR / "digest.json", digest)
    preview_path, html, plain = save_preview(config, digest, candidates, models_used, now)
    print(f"Preview written to {preview_path}")

    context = email_context(config, digest, candidates, models_used, now)
    subject = format_subject(digest, config["digest"]["name"], context["date_label"])
    if dry_run:
        print(f"Dry run complete; not sent: {subject}")
        return

    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        raise RuntimeError("GMAIL_APP_PASSWORD is required to send email")
    send_email(config["email"], subject, html, plain, password)
    save_seen(
        STATE_PATH,
        seen,
        [item["url"] for item in candidates],
        now,
        config["digest"]["seen_retention_days"],
    )
    print(f"Sent: {subject}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gather and email a configurable AI news digest")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="render a sample email without network access")
    mode.add_argument("--dry-run", action="store_true", help="run with live sources but do not email or update seen state")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="path to a TOML configuration file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(ROOT / ".env")
    config = load_config(args.config)
    if args.preview:
        now = datetime.now(timezone.utc)
        path, _, _ = save_preview(config, sample_digest(), [], [], now)
        print(f"Sample preview written to {path}")
        return
    asyncio.run(run_pipeline(config, args.dry_run))


if __name__ == "__main__":
    main()
