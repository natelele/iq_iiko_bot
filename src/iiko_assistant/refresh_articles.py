from __future__ import annotations

import argparse
import sys

from .database import Database
from .import_article import import_one


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh all previously imported iikoHelp articles")
    parser.add_argument("--database", default="data/iiko_assistant.sqlite3")
    parser.add_argument("--timeout", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Database(args.database)
    articles = database.list_articles()
    failed = 0
    try:
        for index, article in enumerate(articles, start=1):
            try:
                import_one(database, url=article.source_url, title=article.title, timeout=args.timeout)
                print(f"[{index}/{len(articles)}] refreshed #{article.id}: {article.title}")
            except Exception as error:
                failed += 1
                print(f"[{index}/{len(articles)}] failed #{article.id}: {error}", file=sys.stderr)
    finally:
        database.close()
    print(f"Completed: {len(articles) - failed} refreshed, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
