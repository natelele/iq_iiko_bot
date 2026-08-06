import tempfile
from pathlib import Path
from unittest import TestCase

from iiko_assistant.database import Database
from iiko_assistant.knowledge import CLARIFY_PRODUCT_ANSWER, KnowledgeBase


class KnowledgeTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "assistant.sqlite3")
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!front/shift",
            "Открытие смены",
            "# Открытие смены\n\nЧтобы открыть смену, авторизуйтесь в кассовом приложении.",
        )

    def tearDown(self) -> None:
        self.database.close()
        self.temp.cleanup()

    def test_unknown_question_is_explicitly_marked_unconfirmed(self) -> None:
        answer = KnowledgeBase(self.database).answer("как подключить спутник")
        self.assertFalse(answer.found)
        self.assertIn("подтверждённой базе знаний", answer.text)

    def test_without_api_key_answer_is_article_excerpt_with_linkable_source(self) -> None:
        answer = KnowledgeBase(self.database, api_key="").answer("как открыть смену")
        self.assertTrue(answer.found)
        self.assertIn("авторизуйтесь", answer.text)
        self.assertEqual(answer.article.source_url, "https://ru.iiko.help/articles/#!front/shift")

    def test_ambiguous_question_requests_product_instead_of_guessing(self) -> None:
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikooffice-9-2/nomenclature",
            "Номенклатура в iikoOffice",
            "Чтобы заполнить номенклатуру, откройте карточку товара в iikoOffice.",
        )
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikofront-9-2/nomenclature",
            "Номенклатура в iikoFront",
            "Чтобы заполнить номенклатуру, откройте карточку товара в iikoFront.",
        )
        answer = KnowledgeBase(self.database, api_key="").answer("как заполнить номенклатуру")
        self.assertFalse(answer.found)
        self.assertEqual(answer.text, CLARIFY_PRODUCT_ANSWER)
        self.assertTrue(answer.product_options)

    def test_product_filter_does_not_add_the_product_name_to_full_text_search(self) -> None:
        front = self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikofront-9-2/guests",
            "Гости",
            "Как добавить нового гостя: откройте список гостей и нажмите Новый гость.",
        )
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikooffice-9-2/guests",
            "Гости",
            "Как добавить нового гостя: откройте справочник гостей.",
        )
        answer = KnowledgeBase(self.database, api_key="").answer(
            "Как добавить нового гостя?", product="iikoFront"
        )
        self.assertTrue(answer.found)
        self.assertEqual(answer.article.id, front.id)

    def test_unknown_product_uses_a_best_article_instead_of_asking_again(self) -> None:
        front = self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikofront-9-2/menu",
            "Внешнее меню во Front",
            "Чтобы создать внешнее меню, откройте раздел меню и нажмите Создать.",
        )
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikooffice-9-2/menu",
            "Внешнее меню в Office",
            "Чтобы создать внешнее меню, откройте настройки и нажмите Создать.",
        )
        answer = KnowledgeBase(self.database, api_key="").answer(
            "Как создать внешнее меню?", accept_ambiguous_product=True
        )
        self.assertTrue(answer.found)
        self.assertFalse(answer.product_options)
        self.assertEqual(answer.article.id, front.id)

    def test_model_can_select_only_a_provided_article(self) -> None:
        article = self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikooffice-9-2/nomenclature",
            "Номенклатура в iikoOffice",
            "Чтобы заполнить номенклатуру, откройте карточку товара в iikoOffice.",
        )
        answer = KnowledgeBase(
            self.database,
            api_key="",
            summary_provider=lambda _: (
                f'{{"article_id": {article.id}, "answer": "Откройте карточку товара и заполните поля."}}'
            ),
        ).answer("iikoOffice: как заполнить номенклатуру")
        self.assertTrue(answer.found)
        self.assertEqual(answer.article.id, article.id)
        self.assertEqual(answer.text, "Откройте карточку товара и заполните поля.")

    def test_model_can_answer_general_question_without_a_matching_article(self) -> None:
        prompts: list[str] = []

        def provider(prompt: str) -> str:
            prompts.append(prompt)
            return "Обычно это зависит от числа лицензий iikoOffice."

        answer = KnowledgeBase(self.database, api_key="", summary_provider=provider).answer(
            "Можно ли работать на одном компьютере без ККТ?"
        )
        self.assertFalse(answer.found)
        self.assertEqual(answer.text, "Обычно это зависит от числа лицензий iikoOffice.")
        self.assertIn("Кратко ответь", prompts[0])

    def test_iikofront_is_inferred_from_a_cash_terminal_question(self) -> None:
        front = self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikofront-9-2/close-shift",
            "Закрытие кассовой смены",
            "В iikoFront на терминале выберите Закрыть кассовую смену.",
        )
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikooffice-9-2/close-shift",
            "Закрытие кассовой смены в бэк-офисе",
            "В iikoOffice можно настроить закрытие кассовой смены.",
        )
        answer = KnowledgeBase(self.database, api_key="").answer(
            "Я хочу закрыть кассовую смену на терминале"
        )
        self.assertTrue(answer.found)
        self.assertEqual(answer.article.id, front.id)

    def test_iikofront_is_inferred_when_the_user_asks_to_select_a_cashier(self) -> None:
        front = self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikofront-9-2/change-cashier",
            "Смена кассира",
            "Вы можете выбрать сотрудника в качестве кассира: откройте раздел Касса и нажмите Сменить кассира.",
        )
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikofront-9-2/close-shift",
            "Закрытие кассовой смены",
            "В конце смены менеджер вместе с кассиром закрывает кассовую смену. Для закрытия выберите отчёт.",
        )
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikooffice-9-2/cashier",
            "Кассир в iikoOffice",
            "Кассир может быть указан в настройках рабочего места.",
        )
        answer = KnowledgeBase(self.database, api_key="").answer("Как выбрать кассира?")
        self.assertTrue(answer.found)
        self.assertEqual(answer.article.id, front.id)

    def test_model_without_an_article_id_falls_back_to_a_linked_article(self) -> None:
        answer = KnowledgeBase(
            self.database,
            api_key="",
            summary_provider=lambda _: '{"article_id": null, "answer": "Не уверен."}',
        ).answer("как открыть смену")
        self.assertTrue(answer.found)
        self.assertEqual(answer.article.source_url, "https://ru.iiko.help/articles/#!front/shift")

    def test_regular_user_question_does_not_choose_release_notes_or_api(self) -> None:
        self.database.upsert_article(
            "https://ru.iiko.help/articles/#!releasenotes/guest-api-change",
            "Изменения API",
            "Создание нового гостя через API изменено.",
        )
        guide = self.database.upsert_article(
            "https://ru.iiko.help/articles/#!iikocard/new-guest",
            "Как добавить гостя",
            "Как добавить гостя: нажмите Создать и заполните данные нового гостя.",
        )
        answer = KnowledgeBase(self.database, api_key="").answer("Как создать нового гостя?")
        self.assertTrue(answer.found)
        self.assertEqual(answer.article.id, guide.id)
