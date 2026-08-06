from unittest import TestCase

from iiko_assistant.iikohelp import IikoHelpUrlError, markdown_url


class IikoHelpUrlTests(TestCase):
    def test_hash_route_becomes_public_markdown_endpoint(self) -> None:
        self.assertEqual(
            markdown_url("https://howto.iiko.help/articles/#!iikofront/getting-started"),
            "https://howto.iiko.help/helper/articles/iikofront/getting-started/?action=getMarkdown",
        )

    def test_non_iikohelp_domains_are_rejected(self) -> None:
        with self.assertRaises(IikoHelpUrlError):
            markdown_url("https://example.com/articles/#!iikofront/getting-started")
