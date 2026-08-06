from datetime import datetime
import tempfile
from pathlib import Path
from unittest import TestCase
from zoneinfo import ZoneInfo

from iiko_assistant.calendar import BusinessCalendar
from iiko_assistant.database import Database
from iiko_assistant.knowledge import KnowledgeBase
from iiko_assistant.models import Answer
from iiko_assistant.service import SupportService


MOSCOW = ZoneInfo("Europe/Moscow")


class ServiceTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "assistant.sqlite3")
        self.database.upsert_article(
            "https://howto.iiko.help/articles/#!front/shift",
            "Открытие смены",
            "# Открытие смены\n\nАвторизуйтесь в кассовом приложении перед открытием смены.",
        )
        self.service = SupportService(self.database, KnowledgeBase(self.database, api_key=""), BusinessCalendar())

    def tearDown(self) -> None:
        self.database.close()
        self.temp.cleanup()

    def test_private_reply_has_source_but_no_not_helpful_button(self) -> None:
        reply = self.service.private_question(chat_id=1, user_id=2, text="как открыть смену")
        self.assertTrue(reply.source_url)
        self.assertFalse(reply.show_not_helpful)

    def test_group_is_silent_during_work_and_notifies_once_after_hours(self) -> None:
        during_work = datetime(2026, 4, 7, 10, 0, tzinfo=MOSCOW)
        after_work = datetime(2026, 4, 7, 18, 0, tzinfo=MOSCOW)
        self.assertIsNone(self.service.group_message(chat_id=-100, moment=during_work))
        notice = self.service.group_message(chat_id=-100, moment=after_work)
        self.assertIn("Проектный отдел сейчас не работает", notice.text)
        self.assertIsNone(self.service.group_message(chat_id=-100, moment=after_work))

    def test_private_question_is_sent_without_chat_history(self) -> None:
        class CapturingKnowledge:
            def __init__(self) -> None:
                self.question = ""
                self.product: str | None = None

            def answer(
                self, question: str, *, product: str | None = None, accept_ambiguous_product: bool = False
            ) -> Answer:
                self.question = question
                self.product = product
                self.accept_ambiguous_product = accept_ambiguous_product
                return Answer("Ответ", None, False)

        self.database.create_question(
            chat_id=1, user_id=2, is_group=False, question="У меня iikoOffice 9.2", answer="", article_id=None, source_url=None,
        )
        knowledge = CapturingKnowledge()
        service = SupportService(self.database, knowledge, BusinessCalendar())
        service.private_question(chat_id=1, user_id=2, text="А где открыть номенклатуру?")
        self.assertEqual(knowledge.question, "А где открыть номенклатуру?")

    def test_product_button_reuses_the_original_question(self) -> None:
        class CapturingKnowledge:
            def __init__(self) -> None:
                self.question = ""
                self.product: str | None = None

            def answer(
                self, question: str, *, product: str | None = None, accept_ambiguous_product: bool = False
            ) -> Answer:
                self.question = question
                self.product = product
                return Answer("Ответ", None, False)

        original = self.database.create_question(
            chat_id=1, user_id=2, is_group=False, question="Как создать номенклатуру?",
            answer="", article_id=None, source_url=None,
        )
        knowledge = CapturingKnowledge()
        service = SupportService(self.database, knowledge, BusinessCalendar())
        reply = service.select_product(question_id=original.id, user_id=2, product="iikoOffice")
        self.assertEqual(reply.text, "Ответ")
        self.assertEqual(knowledge.question, "Как создать номенклатуру?")
        self.assertEqual(knowledge.product, "iikoOffice")

    def test_unknown_product_asks_where_the_action_is_performed(self) -> None:
        original = self.database.create_question(
            chat_id=1, user_id=2, is_group=False, question="Как создать внешнее меню?",
            answer="", article_id=None, source_url=None,
        )
        reply = self.service.select_product(question_id=original.id, user_id=2, product="unknown")
        self.assertIn("Где вы выполняете", reply.text)
        self.assertEqual(reply.question_id, original.id)
        self.assertIn(("На кассе или терминале", "iikoFront"), reply.workplace_options)
