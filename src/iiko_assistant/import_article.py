from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .database import Database
from .iikohelp import fetch_article


def _title_from_markdown(markdown: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def import_one(database: Database, *, url: str, title: str | None = None, timeout: int = 20) -> int:
    downloaded = fetch_article(url, timeout=timeout)
    article = database.upsert_article(
        downloaded.source_url,
        title or _title_from_markdown(downloaded.markdown, "Статья iikoHelp"),
        downloaded.markdown,
    )
    return article.id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import one permitted iikoHelp article into SQLite")
    parser.add_argument("--url", required=True, help="Public iikoHelp article URL")
    parser.add_argument("--title", help="Optional title; otherwise the first Markdown heading is used")
    parser.add_argument("--database", default="data/iiko_assistant.sqlite3")
    parser.add_argument("--timeout", type=int, default=20)
    # Kept for the command documented in the original project context.
    parser.add_argument("--from-iikohelp", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Database(Path(args.database))
    try:
        try:
            article_id = import_one(database, url=args.url, title=args.title, timeout=args.timeout)
        except (OSError, ValueError) as error:
            print(f"Import failed: {error}", file=sys.stderr)
            return 1
    finally:
        database.close()
    print(f"Imported article #{article_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
