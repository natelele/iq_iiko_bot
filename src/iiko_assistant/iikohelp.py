from __future__ import annotations

import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass

import certifi


ALLOWED_HOSTS = frozenset({"howto.iiko.help", "ru.iiko.help"})


class IikoHelpUrlError(ValueError):
    pass


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def secure_https_handler() -> urllib.request.HTTPSHandler:
    """Use certifi so a Python virtualenv has an up-to-date CA bundle."""
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.HTTPSHandler(context=context)


@dataclass(frozen=True)
class ImportedArticle:
    source_url: str
    markdown_url: str
    markdown: str


def _validate_host(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise IikoHelpUrlError("Only HTTPS articles from howto.iiko.help and ru.iiko.help are allowed")
    return parsed


def markdown_url(source_url: str) -> str:
    parsed = _validate_host(source_url)
    fragment = urllib.parse.unquote(parsed.fragment).lstrip("!")
    match = re.fullmatch(r"([\w.-]+)/([\w.-]+)", fragment)
    if not match:
        path_match = re.search(r"/(?:helper/)?articles/([\w.-]+)/([\w.-]+)/?", parsed.path)
        if not path_match:
            raise IikoHelpUrlError("The URL must contain an iikoHelp article project and article slug")
        project, article = path_match.groups()
    else:
        project, article = match.groups()
    return f"https://{parsed.hostname}/helper/articles/{project}/{article}/?action=getMarkdown"


def fetch_article(source_url: str, timeout: int = 20) -> ImportedArticle:
    endpoint = markdown_url(source_url)
    opener = urllib.request.build_opener(_SafeRedirect(), secure_https_handler())
    request = urllib.request.Request(endpoint, headers={"User-Agent": "iiko-project-assistant/0.1"})
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        _validate_host(final_url)
        markdown = response.read().decode("utf-8")
    if not markdown.strip():
        raise ValueError("iikoHelp returned an empty article")
    return ImportedArticle(source_url=source_url, markdown_url=endpoint, markdown=markdown)
