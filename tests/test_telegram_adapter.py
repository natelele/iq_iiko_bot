from unittest import TestCase

from iiko_assistant.telegram_adapter import START_MESSAGE, _private_question_text


class TelegramAdapterTests(TestCase):
    def test_start_message_explains_how_to_ask_for_help(self) -> None:
        self.assertIn("помощник проектного отдела iiko", START_MESSAGE)
        self.assertIn("/a ваш вопрос", START_MESSAGE)

    def test_private_question_accepts_the_group_style_prefix(self) -> None:
        self.assertEqual(_private_question_text("/a Как выбрать кассира?"), "Как выбрать кассира?")
        self.assertEqual(_private_question_text("/a@ProjectDepartment_helper вопрос"), "вопрос")
        self.assertEqual(_private_question_text("Как выбрать кассира?"), "Как выбрать кассира?")
