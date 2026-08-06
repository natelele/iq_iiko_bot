from unittest import TestCase

from iiko_assistant.sitemap_import import discover_article_urls


INDEX = """<?xml version=\"1.0\"?>
<sitemapindex xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
  <sitemap><loc>https://ru.iiko.help/sitemaps/front.xml</loc></sitemap>
  <sitemap><loc>https://howto.iiko.help/sitemaps/current.xml</loc></sitemap>
</sitemapindex>"""

FRONT = """<?xml version=\"1.0\"?>
<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
  <url><loc>https://ru.iiko.help/articles/#!iikofront-8-7/old</loc></url>
  <url><loc>https://ru.iiko.help/articles/#!iikofront-8-8/current</loc></url>
  <url><loc>https://ru.iiko.help/articles/#!iikofront-9-x/latest</loc></url>
  <url><loc>https://example.com/articles/#!iikofront-9-2/untrusted</loc></url>
</urlset>"""

CURRENT = """<?xml version=\"1.0\"?>
<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
  <url><loc>https://howto.iiko.help/articles/#!iikofront/getting-started</loc></url>
</urlset>"""


class SitemapImportTests(TestCase):
    def test_discovery_keeps_unversioned_and_versions_from_minimum(self) -> None:
        payloads = {
            "https://ru.iiko.help/sitemap.xml": INDEX,
            "https://ru.iiko.help/sitemaps/front.xml": FRONT,
            "https://howto.iiko.help/sitemaps/current.xml": CURRENT,
        }
        result = discover_article_urls(
            sitemap_urls=["https://ru.iiko.help/sitemap.xml"], fetcher=payloads.__getitem__
        )
        self.assertEqual(
            result.article_urls,
            [
                "https://howto.iiko.help/articles/#!iikofront/getting-started",
                "https://ru.iiko.help/articles/#!iikofront-8-8/current",
                "https://ru.iiko.help/articles/#!iikofront-9-x/latest",
            ],
        )
        self.assertEqual(result.failed_sitemaps, [])

    def test_discovery_records_broken_sitemap_without_crashing(self) -> None:
        result = discover_article_urls(
            sitemap_urls=["https://ru.iiko.help/sitemap.xml"], fetcher=lambda _: "not XML"
        )
        self.assertEqual(result.article_urls, [])
        self.assertEqual(len(result.failed_sitemaps), 1)
