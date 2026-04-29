"""
pull_digest.py  — run locally after the GH Action commits a digest.

Usage:
    python pull_digest.py            # uses today's date
    python pull_digest.py 2026-04-28 # specific date

Steps:
    1. git pull (so digests/ is up to date)
    2. reads digests/YYYY-MM-DD.json
    3. writes 00-Inbox/digest-review-YYYY-MM-DD.md to the Obsidian vault
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from sources import VAULT_PATH

SCRIPT_DIR = Path(__file__).parent
DIGESTS_DIR = SCRIPT_DIR / "digests"
CANDIDATES_DIR = SCRIPT_DIR / "candidates"
VAULT = SCRIPT_DIR / VAULT_PATH
INBOX = VAULT / "00-Inbox"

SECTION_LABELS = {
    "top_stories": "Top Stories",
    "important_developments": "Important Developments",
    "builder_tools": "Builder Tools & Open Source",
    "market_signals": "Market Signals",
    "other_notable": "Other Notable",
}


def git_pull():
    result = subprocess.run(["git", "pull"], cwd=SCRIPT_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"git pull warning: {result.stderr.strip()}")
    else:
        print(result.stdout.strip() or "Already up to date.")


def format_date_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return d.strftime("%A, %B %-d, %Y")


def render_article(item: dict, number: int | None, is_top: bool) -> str:
    title = item.get("title", "Untitled")
    url = item.get("url", "")
    source = item.get("source", "")
    what = item.get("what", "")
    why = item.get("why", "")

    link = f"[{title}]({url})" if url else title
    prefix = f"### {number}. " if number is not None else "### "
    lines = [f"{prefix}{link}", f"*{source}*", ""]
    if what:
        lines.append(f"> {what}")
    if is_top and why:
        lines.append(f">")
        lines.append(f"> *{why}*")
    lines.append("")
    lines.append("rating: \nnote: ")
    return "\n".join(lines)


def build_note(digest: dict, today: str, candidates_count: int | None = None) -> str:
    label = format_date_label(today)
    subject = digest.get("subject_line", "")

    articles_count = sum(len(digest.get(k, [])) for k in SECTION_LABELS)

    # Frontmatter
    fm = ["---", f"date: {today}", "tags:", "  - digest-review", "rated: false",
          f"articles: {articles_count}"]
    if candidates_count is not None:
        fm.append(f"candidates: {candidates_count}")
    if subject:
        fm.append(f"subject: {subject}")
    fm.append("---")

    subtitle = f"*{articles_count} articles"
    if candidates_count is not None:
        subtitle += f" · {candidates_count} candidates"
    subtitle += "*"

    tldr_lines = "\n".join(f"> - {b}" for b in digest.get("tldr", []))

    blocks = fm + [
        "",
        f"# AI Daily — {label}",
        subtitle,
        "",
        "> [!abstract] Today at a Glance",
        tldr_lines,
        "",
        "---",
    ]

    for key, label_str in SECTION_LABELS.items():
        items = digest.get(key, [])
        if not items:
            continue
        is_top = key == "top_stories"
        blocks.append(f"\n## {label_str}")
        for i, item in enumerate(items, 1):
            blocks.append("")
            blocks.append(render_article(item, i if is_top else None, is_top))
            if is_top:
                blocks.append("\n---")
        if not is_top:
            blocks.append("\n---")

    return "\n".join(blocks) + "\n"


def main():
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

    print("Pulling latest from remote...")
    git_pull()

    digest_path = DIGESTS_DIR / f"{today}.json"
    if not digest_path.exists():
        print(f"No digest found for {today} at {digest_path}")
        print("The GH Action may not have run yet, or the date is wrong.")
        sys.exit(1)

    digest = json.loads(digest_path.read_text())

    candidates_count = None
    candidates_path = CANDIDATES_DIR / f"{today}.json"
    if candidates_path.exists():
        candidates_count = len(json.loads(candidates_path.read_text()))

    note = build_note(digest, today, candidates_count)

    INBOX.mkdir(parents=True, exist_ok=True)
    out_path = INBOX / f"digest-review-{today}.md"
    out_path.write_text(note)
    print(f"Review note written: {out_path}")
    print("Open Obsidian, fill in each `rating:` line, then run refine.py when ready.")


if __name__ == "__main__":
    main()
