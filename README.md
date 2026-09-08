# Caio's AI Brief

A configurable RSS-to-email AI news digest.

## Customize it

Most changes happen in [`config.toml`](config.toml):

- Add, remove, disable, prioritize, or limit RSS sources.
- Change the digest name, article limits, recency window, and models.
- Change recipients, visible metadata, section names, and email colors.

Editorial instructions live in [`prompts/`](prompts/). Email layout lives in
[`templates/email.html`](templates/email.html).

## Commands

```bash
# Render a sample email without API keys or network access
python run.py --preview

# Run against live sources without emailing or updating seen state
python run.py --dry-run

# Gather, generate, email, and update seen state
python run.py
```

Generated candidates, digest JSON, and `preview.html` go into the ignored
`output/` directory. The only committed runtime state is `state/seen.json`,
which is updated only after an email sends successfully.

## Secrets

Local runs load these values from `.env`. GitHub Actions reads them from
repository secrets.

```dotenv
GEMINI_API_KEY=...
GMAIL_APP_PASSWORD=...
```
