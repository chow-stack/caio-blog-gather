# AI Brief

**A configurable, LLM-curated AI news digest delivered to your inbox.**

AI Brief pulls recent stories from RSS feeds you choose, has Google Gemini pick the ones worth reading, reads each full article, and emails you a clean, sectioned digest with top stories, important developments, builder tools, market signals, and more. It runs on a schedule with GitHub Actions and remembers what it has already sent, so you never see the same story twice.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab) ![Gemini](https://img.shields.io/badge/LLM-Gemini-4285f4) ![GitHub Actions](https://img.shields.io/badge/runs%20on-GitHub%20Actions-2088ff) ![License: MIT](https://img.shields.io/badge/license-MIT-blue)

---

## Contents

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Running on GitHub Actions](#running-on-github-actions)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Security](#security)
- [License](#license)

## How it works

```
RSS feeds ──▶ gather + dedupe ──▶ recency filter ──▶ Gemini: select ──▶ crawl4ai: read full articles
                  ▲                                                              │
                  │                                                              ▼
           state/seen.json ◀── record sent URLs ◀── Gmail SMTP ◀── Jinja2 email ◀── Gemini: write digest
```

1. **Gather.** Feeds in `config.toml` are fetched with `feedparser`. Junk links and anything already in `state/seen.json` are dropped.
2. **Filter.** Only items published within `recency_hours` are kept.
3. **Select.** Gemini ranks the candidates against the editorial guidance in [`prompts/select.md`](prompts/select.md) and picks up to `max_articles`.
4. **Read.** [crawl4ai](https://github.com/unclecode/crawl4ai) uses a headless browser to scrape the full text of each selected article.
5. **Write.** Gemini writes a structured digest from that text, following [`prompts/digest.md`](prompts/digest.md). The output is validated against the scraped sources.
6. **Send.** The digest is rendered with [`templates/email.html`](templates/email.html) (plus a plain-text alternative) and sent over Gmail SMTP.
7. **Remember.** Delivered URLs go into `state/seen.json`, which keeps them for `seen_retention_days`. State is written only after an email sends successfully.

If the primary model fails or is rate-limited, the configured fallback models are tried in order.

## Quick start

### Prerequisites

- Python 3.11 or newer (CI uses 3.14)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)
- A Gmail account with an [app password](https://myaccount.google.com/apppasswords) (2-Step Verification must be on)

### Install

```bash
git clone https://github.com/chow-stack/caio-blog-gather.git
cd caio-blog-gather
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
crawl4ai-setup            # installs the headless browser used for article scraping
```

### Configure secrets

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Gemini API key |
| `GMAIL_APP_PASSWORD` | App password for the sending Gmail account |
| `DIGEST_EMAIL_FROM` | Sending address (the Gmail account above) |
| `DIGEST_EMAIL_TO` | Recipient addresses, comma-separated |

### Run

```bash
# Render a sample email with no API keys or network access
python run.py --preview

# Run against live sources without emailing or updating state
python run.py --dry-run

# Gather, generate, email, and update state
python run.py
```

Previews and intermediate files (`candidates.json`, the digest JSON, `preview.html`) are written to the git-ignored `output/` directory.

## Configuration

Nearly everything is controlled from [`config.toml`](config.toml). Pass `--config path/to/other.toml` to use a different file.

| Section | What it controls |
|---|---|
| `[digest]` | Digest name and tagline, `max_articles`, `recency_hours`, `top_stories`, `seen_retention_days` |
| `[models]` | `primary` Gemini model and an ordered list of `fallbacks` |
| `[email]` | SMTP host and port, and optional metadata in the footer. Prefer the `DIGEST_EMAIL_*` environment variables over hard-coding addresses here. |
| `[theme]` | Email colors (accent, background, surface, text, muted, border) |
| `[sections]` | Section headings in the email |
| `[[sources]]` | One block per feed: `name`, `url`, `enabled`, and `priority` |

Add a source:

```toml
[[sources]]
name = "My Favorite Blog"
url = "https://example.com/feed.xml"
enabled = true
priority = 4
```

To change the editorial voice, or what counts as newsworthy, edit the prompts in [`prompts/`](prompts/). To change the layout, edit [`templates/email.html`](templates/email.html).

## Running on GitHub Actions

The workflow in [`.github/workflows/gather.yml`](.github/workflows/gather.yml) installs dependencies, runs the pipeline, and commits the updated `state/seen.json` back to the repository.

1. Fork or clone this repository to your account.
2. Under **Settings → Secrets and variables → Actions**, add these repository secrets: `GEMINI_API_KEY`, `GMAIL_APP_PASSWORD`, `DIGEST_EMAIL_FROM`, and `DIGEST_EMAIL_TO`.
3. Trigger it from the **Actions** tab (**Daily Gather → Run workflow**). To run it on a schedule, add a `schedule` trigger:

   ```yaml
   on:
     workflow_dispatch:
     schedule:
       - cron: "0 12 * * *"   # every day at 12:00 UTC
   ```

Secrets are never available to workflows triggered from forks, so outside pull requests can't read them.

## Testing

```bash
python -m unittest discover -s tests
```

The tests cover config loading, feed URL validation, seen-state migration and pruning, HTML escaping in the email template, digest validation against the scraped sources, and the Gemini call path, all without network access.

## Project structure

```
run.py                  CLI entry point: --preview, --dry-run, or a full run
config.toml             Sources, models, email, theme, and section settings
digest/
├── config.py           Loads and validates config, applies environment overrides
├── feeds.py            RSS gathering, junk filtering, recency filter
├── generator.py        Gemini selection and writing, crawl4ai scraping, output validation
├── email.py            HTML and plain-text rendering, SMTP delivery
└── state.py            Seen-URL store with retention
prompts/                Editorial prompts for selection and writing
templates/email.html    Jinja2 email template
state/seen.json         URLs already delivered (committed by the workflow)
tests/                  Unit tests
```

## Security

- Secrets come only from environment variables (`.env` locally, or GitHub Actions secrets). `.env` is git-ignored, and `.env.example` contains no values.
- Use a Gmail **app password**, never your account password. You can revoke it at any time.
- Article text is sent to Gemini for summarization. Don't add private or paywalled feeds you aren't allowed to share with a third-party API.

## License

Released under the [MIT License](LICENSE).
