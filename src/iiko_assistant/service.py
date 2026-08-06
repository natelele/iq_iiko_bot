from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .calendar import BusinessCalendar
from .database import Database
from .knowledge import KnowledgeBase
from .models import Answer, FeedbackResult


OFF_HOURS_NOTICE = """🕐 Проектный отдел сейчас не работает.
Мы вернёмся к работе в {next_start} по МСК.

Я могу попробовать помочь самостоятельно. Сформулируйте вопрос через команду:
/a ваш вопрос"""

PRODUCT_OPTIONS = ("iikoOffice", "iikoFront", "iikoChain", "iikoWeb")
UNKNOWN_PRODUCT_OPTION = "Не знаю"
WORKPLACE_OPTIONS = (
    ("На кассе или терминале", "iikoFront"),
    ("В офисной программе", "iikoOffice"),
    ("В браузере", "iikoWeb"),
    ("Для сети ресторанов", "iikoChain"),
)
WORKPLACE_CLARIFICATION = "Ничего страшного. Где вы выполняете это действие?"


@dataclass(frozen=True)
class ServiceReply:
    text: str
    question_id: int | None = None
    source_url: str | None = None
    show_not_helpful: bool = False
    product_options: tuple[str, ...] = ()
    workplace_options: tuple[tuple[str, str], ...] = ()


class SupportService:
    """Messenger-neutral support scenarios used by Telegram and future MAX adapters."""

    def __init__(self, database: Database, knowledge: KnowledgeBase, calendar: BusinessCalendar) -> None:
        self.database = database
        self.knowledge = knowledge
        self.calendar = calendar

    def private_question(self, *, chat_id: int, user_id: int, text: str) -> ServiceReply:
        return self._answer(chat_id=chat_id, user_id=user_id, text=text, is_group=False, with_feedback=False)

    def group_message(self, *, chat_id: int, moment: datetime | None = None) -> ServiceReply | None:
        """React once per chat and continuous off-hours period; never during work."""
        if self.calendar.is_working_time(moment):
            return None
        period = self.calendar.off_hours_period_key(moment)
        if not self.database.mark_group_notified(chat_id, period):
            return None
        return ServiceReply(OFF_HOURS_NOTICE.format(next_start=self.calendar.format_next_work_start(moment)))

    def group_question(
        self, *, chat_id: int, user_id: int, text: str, moment: datetime | None = None
    ) -> ServiceReply | None:
        if self.calendar.is_working_time(moment):
            return None
        clean = text.strip()
        if not clean:
            return ServiceReply("Напишите вопрос после команды: /a ваш вопрос")
        return self._answer(chat_id=chat_id, user_id=user_id, text=clean, is_group=True, with_feedback=True)

    def _answer(
        self, *, chat_id: int, user_id: int, text: str, is_group: bool, with_feedback: bool,
        product: str | None = None,
        accept_ambiguous_product: bool = False,
    ) -> ServiceReply:
        answer: Answer = self.knowledge.answer(
            text, product=product, accept_ambiguous_product=accept_ambiguous_product,
        )
        question = self.database.create_question(
            chat_id=chat_id,
            user_id=user_id,
            is_group=is_group,
            question=text,
            answer=answer.text,
            article_id=answer.article.id if answer.article else None,
            source_url=answer.article.source_url if answer.article else None,
        )
        return ServiceReply(
            text=answer.text,
            question_id=question.id,
            source_url=question.source_url,
            show_not_helpful=with_feedback and answer.found,
            product_options=PRODUCT_OPTIONS + (UNKNOWN_PRODUCT_OPTION,) if answer.product_options else (),
        )

    def select_product(self, *, question_id: int, user_id: int, product: str) -> ServiceReply:
        if product != "unknown" and product not in PRODUCT_OPTIONS:
            return ServiceReply("Не удалось распознать продукт. Задайте вопрос ещё раз.")
        original = self.database.get_question(question_id)
        if original is None:
            return ServiceReply("Этот вопрос больше недоступен. Задайте новый.")
        if original.user_id != user_id:
            return ServiceReply("Эта кнопка доступна только автору вопроса.")
        if product == "unknown":
            return ServiceReply(
                text=WORKPLACE_CLARIFICATION,
                question_id=original.id,
                workplace_options=WORKPLACE_OPTIONS,
            )
        return self._answer(
            chat_id=original.chat_id,
            user_id=user_id,
            text=original.question,
            is_group=original.is_group,
            with_feedback=original.is_group,
            product=product,
        )

    def not_helpful(self, *, question_id: int, user_id: int) -> ServiceReply:
        result: FeedbackResult = self.database.register_not_helpful(question_id, user_id)
        messages = {
            "not_owner": "Эта кнопка доступна только автору вопроса.",
            "not_found": "Этот вопрос больше недоступен. Отправьте новый через /a.",
            "already_escalated": "Ваш вопрос уже зафиксирован. Как только проектный отдел будет на связи, коллеги помогут разобраться.",
        }
        if result.kind == "ticket_created":
            return ServiceReply(
                "Принял, зафиксировал ваш вопрос. Как только проектный отдел будет на связи, "
                "коллеги помогут разобраться."
            )
        return ServiceReply(messages[result.kind])
