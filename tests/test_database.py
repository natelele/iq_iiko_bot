import tempfile
from pathlib import Path
from unittest import TestCase

from iiko_assistant.database import Database


class DatabaseTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "assistant.sqlite3")
        self.article = self.database.upsert_article(
            "https://howto.iiko.help/articles/#!iikofront/cashier",
            "Кассовое приложение",
            "# Кассовое приложение\n\nОткройте кассовое приложение и выполните настройку смены.",
        )

    def tearDown(self) -> None:
        self.database.close()
        self.temp.cleanup()

    def test_fts_search_returns_imported_article(self) -> None:
        found = self.database.search_articles("как настроить кассовое приложение")
        self.assertEqual([item.id for item in found], [self.article.id])

    def test_article_source_urls_returns_only_urls(self) -> None:
        self.assertEqual(self.database.article_source_urls(), {self.article.source_url})

    def test_group_notice_is_idempotent_per_period(self) -> None:
        self.assertTrue(self.database.mark_group_notified(-100, "2026-04-06T08:00:00+03:00"))
        self.assertFalse(self.database.mark_group_notified(-100, "2026-04-06T08:00:00+03:00"))

    def test_first_feedback_creates_exactly_one_ticket(self) -> None:
        question = self.database.create_question(
            chat_id=-100, user_id=77, is_group=True, question="Не работает смена", answer="Проверьте смену",
            article_id=self.article.id, source_url=self.article.source_url,
        )
        created = self.database.register_not_helpful(question.id, 77)
        self.assertEqual(created.kind, "ticket_created")
        self.assertEqual(self.database.register_not_helpful(question.id, 77).kind, "already_escalated")
        self.assertEqual(self.database.get_question_ticket(question.id), created.ticket_number)

    def test_other_user_cannot_escalate_question(self) -> None:
        question = self.database.create_question(
            chat_id=-100, user_id=77, is_group=True, question="Вопрос", answer="Ответ",
            article_id=None, source_url=None,
        )
        self.assertEqual(self.database.register_not_helpful(question.id, 78).kind, "not_owner")
