from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .database import Database
from .iikohelp import ALLOWED_HOSTS, IikoHelpUrlError, markdown_url, secure_https_handler
from .import_article import import_one


DEFAULT_SITEMAPS = (
    "https://howto.iiko.help/sitemap.xml",
    "https://ru.iiko.help/sitemap.xml",
)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_sitemap_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class SitemapDiscovery:
    article_urls: list[str]
    failed_sitemaps: list[tuple[str, str]]


def _validate_sitemap_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise IikoHelpUrlError("Only HTTPS sitemaps from permitted iikoHelp domains are allowed")
    return parsed


def fetch_sitemap(url: str, timeout: int = 20) -> str:
    _validate_sitemap_url(url)
    opener = urllib.request.build_opener(_SafeRedirect(), secure_https_handler())
    request = urllib.request.Request(url, headers={"User-Agent": "iiko-project-assistant/0.1"})
    with opener.open(request, timeout=timeout) as response:
        _validate_sitemap_url(response.geturl())
        return response.read().decode("utf-8-sig")


def _tag_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _locs(payload: str) -> tuple[str, list[str]]:
    root = ElementTree.fromstring(payload)
    locs = [
        child.text.strip()
        for element in root
        if _tag_name(element) in {"url", "sitemap"}
        for child in element
        if _tag_name(child) == "loc" and child.text
    ]
    return _tag_name(root), locs


def _article_version(url: str) -> tuple[int, int] | None:
    project = urllib.parse.unquote(urllib.parse.urlparse(url).fragment).lstrip("!").split("/", 1)[0]
    match = re.search(r"-(\d+)-(\d+|x)$", project)
    if not match:
        return None
    major, minor = match.groups()
    return int(major), 999 if minor == "x" else int(minor)


def _is_selected_article(url: str, min_version: tuple[int, int]) -> bool:
    try:
        markdown_url(url)
    except IikoHelpUrlError:
        return False
    version = _article_version(url)
    return version is None or version >= min_version


def discover_article_urls(
    *,
    sitemap_urls: Iterable[str] = DEFAULT_SITEMAPS,
    min_version: tuple[int, int] = (8, 8),
    fetcher: Callable[[str], str] = fetch_sitemap,
) -> SitemapDiscovery:
    """Collect permitted iikoHelp article links from sitemap indexes and urlsets."""
    pending = deque(sitemap_urls)
    seen_sitemaps: set[str] = set()
    articles: set[str] = set()
    failures: list[tuple[str, str]] = []

    while pending:
        sitemap_url = pending.popleft()
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        try:
            _validate_sitemap_url(sitemap_url)
            root_tag, locs = _locs(fetcher(sitemap_url))
        except (OSError, ValueError, ElementTree.ParseError) as error:
            failures.append((sitemap_url, str(error)))
            continue
        if root_tag == "sitemapindex":
            pending.extend(locs)
        elif root_tag == "urlset":
            articles.update(url for url in locs if _is_selected_article(url, min_version))
        else:
            failures.append((sitemap_url, f"unexpected XML root: {root_tag}"))
    return SitemapDiscovery(sorted(articles), failures)


def _parse_version(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)", value)
    if not match:
        raise argparse.ArgumentTypeError("Version must look like 8.8")
    return int(match.group(1)), int(match.group(2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import permitted iikoHelp articles from official sitemaps")
    parser.add_argument("--database", default="data/iiko_assistant.sqlite3")
    parser.add_argument("--min-version", type=_parse_version, default=(8, 8), help="Keep versioned articles from this version (default: 8.8)")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.15, help="Seconds between article downloads (default: 0.15)")
    parser.add_argument("--progress-every", type=int, default=25, help="Print successful-import progress after this many articles (default: 25)")
    parser.add_argument("--include-existing", action="store_true", help="Re-download articles already present in the database")
    parser.add_argument("--discover-only", action="store_true", help="List matching article URLs without writing to SQLite")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.progress_every < 1:
        raise SystemExit("--progress-every must be at least 1")
    discovery = discover_article_urls(min_version=args.min_version, fetcher=lambda url: fetch_sitemap(url, args.timeout))
    for sitemap, error in discovery.failed_sitemaps:
        print(f"Sitemap failed: {sitemap}: {error}", file=sys.stderr)
    if args.discover_only:
        print("\n".join(discovery.article_urls))
        print(f"Found: {len(discovery.article_urls)} articles, {len(discovery.failed_sitemaps)} sitemap failures.", file=sys.stderr)
        return 1 if discovery.failed_sitemaps else 0
    if not discovery.article_urls:
        print("No matching articles found.", file=sys.stderr)
        return 1

    database = Database(args.database)
    existing_urls = database.article_source_urls()
    urls_to_import = (
        discovery.article_urls
        if args.include_existing
        else [url for url in discovery.article_urls if url not in existing_urls]
    )
    if not urls_to_import:
        database.close()
        print(f"Completed: all {len(discovery.article_urls)} matching articles are already in the database.")
        return 1 if discovery.failed_sitemaps else 0
    skipped = len(discovery.article_urls) - len(urls_to_import)
    if skipped:
        print(f"Skipped {skipped} articles already present in the database.")
    succeeded = 0
    failed = 0
    try:
        for index, url in enumerate(urls_to_import, start=1):
            try:
                article_id = import_one(database, url=url, timeout=args.timeout)
                succeeded += 1
                if index % args.progress_every == 0 or index == len(urls_to_import):
                    print(f"[{index}/{len(urls_to_import)}] imported: {succeeded} succeeded, {failed} failed")
            except Exception as error:  # individual stale pages must not stop the complete import
                failed += 1
                print(f"[{index}/{len(discovery.article_urls)}] failed: {url}: {error}", file=sys.stderr)
            if args.delay > 0 and index < len(urls_to_import):
                time.sleep(args.delay)
    finally:
        database.close()
    print(f"Completed: {succeeded} imported, {failed} failed; {len(discovery.failed_sitemaps)} sitemap failures.")
    return 1 if failed or discovery.failed_sitemaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
