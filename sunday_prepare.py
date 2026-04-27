"""
Step 1 of the Sunday blog build.
Reads all candidate files from the current ISO week, merges them,
writes candidates/this_week.json, and prints a formatted list for Claude to review.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from sources import VAULT_PATH

SCRIPT_DIR = Path(__file__).parent
CANDIDATES_DIR = SCRIPT_DIR / "candidates"


def current_week_files() -> list[Path]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    files = []
    for i in range((today - monday).days + 1):
        f = CANDIDATES_DIR / f"{(monday + timedelta(days=i)).isoformat()}.json"
        if f.exists():
            files.append(f)
    return files


def load_and_merge(files: list[Path]) -> list[dict]:
    seen, items = set(), []
    for f in files:
        for item in json.loads(f.read_text()):
            if item["url"] not in seen:
                seen.add(item["url"])
                items.append(item)
    return items


def main():
    files = current_week_files()
    if not files:
        print("No candidate files found for this week.")
        return

    items = load_and_merge(files)
    if not items:
        print("No candidates this week.")
        return

    out = CANDIDATES_DIR / "this_week.json"
    out.write_text(json.dumps(items, indent=2, ensure_ascii=False))

    print(f"Found {len(items)} candidates across {len(files)} day(s).\n")
    print("=" * 60)
    print("CANDIDATES FOR REVIEW")
    print("=" * 60)

    by_source: dict[str, list[dict]] = {}
    for item in items:
        by_source.setdefault(item["source"], []).append(item)

    n = 1
    for source, source_items in by_source.items():
        print(f"\n### {source}")
        for item in source_items:
            print(f"  {n:>3}. [{item['date_found']}] {item['title']}")
            print(f"       {item['url']}")
            n += 1

    print("\n" + "=" * 60)
    print(f"Full list saved to: {out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
