from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .database import Database
from .import_article import import_one


def _urls(path: Path) -> list[str]:
    result: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            result.append(line)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bulk import a one-URL-per-line iikoHelp list")
    parser.add_argument("--urls-file", required=True, type=Path)
    parser.add_argument("--database", default="data/iiko_assistant.sqlite3")
    parser.add_argument("--timeout", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    urls = _urls(args.urls_file)
    if not urls:
        print("The URL list is empty.", file=sys.stderr)
        return 2
    database = Database(args.database)
    succeeded = 0
    failed: list[tuple[str, str]] = []
    try:
        for index, url in enumerate(urls, start=1):
            try:
                article_id = import_one(database, url=url, timeout=args.timeout)
                succeeded += 1
                print(f"[{index}/{len(urls)}] imported #{article_id}: {url}")
            except Exception as error:  # a single stale source must not stop the batch
                failed.append((url, str(error)))
                print(f"[{index}/{len(urls)}] failed: {url}: {error}", file=sys.stderr)
    finally:
        database.close()
    print(f"Completed: {succeeded} imported, {len(failed)} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
