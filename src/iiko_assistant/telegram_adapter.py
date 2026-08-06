from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from .calendar import BusinessCalendar
from .database import Database
from .knowledge import KnowledgeBase
from .service import UNKNOWN_PRODUCT_OPTION, ServiceReply, SupportService
from .settings import load_dotenv, project_path

logger = logging.getLogger(__name__)


START_MESSAGE = """Здравствуйте! Я — помощник проектного отдела iiko.

Напишите мне вопрос в этом чате обычным сообщением — я поищу ответ в базе iikoHelp и пришлю ссылку на статью.

В группе я отвечаю на команду /a ваш вопрос в нерабочее время."""


def _telegram():
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
    except ImportError as error:  # makes imports and tests work before optional runtime dependency is installed
        raise RuntimeError("Install dependencies first: python -m pip install -e .") from error
    return InlineKeyboardButton, InlineKeyboardMarkup, Update, Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters


def _keyboard(reply: ServiceReply):
    InlineKeyboardButton, InlineKeyboardMarkup, *_ = _telegram()
    buttons = []
    if reply.source_url:
        buttons.append([InlineKeyboardButton("Подробнее на iikoHelp", url=reply.source_url)])
    if reply.show_not_helpful and reply.question_id is not None:
        buttons.append([InlineKeyboardButton("Не помогло", callback_data=f"nh:{reply.question_id}")])
    if reply.product_options and reply.question_id is not None:
        product_buttons = []
        for product in reply.product_options:
            callback_product = "unknown" if product == UNKNOWN_PRODUCT_OPTION else product
            product_buttons.append(
                InlineKeyboardButton(product, callback_data=f"product:{reply.question_id}:{callback_product}")
            )
        buttons.extend(product_buttons[index:index + 2] for index in range(0, len(product_buttons), 2))
    if reply.workplace_options and reply.question_id is not None:
        workplace_buttons = [
            InlineKeyboardButton(label, callback_data=f"product:{reply.question_id}:{product}")
            for label, product in reply.workplace_options
        ]
        buttons.extend(workplace_buttons[index:index + 2] for index in range(0, len(workplace_buttons), 2))
    return InlineKeyboardMarkup(buttons) if buttons else None


async def _send(message, reply: ServiceReply) -> None:
    await message.reply_text(reply.text, reply_markup=_keyboard(reply))


def _private_question_text(text: str) -> str:
    """Accept the group-style /a prefix in a private chat too."""
    return re.sub(r"^/a(?:@\w+)?(?:\s+|$)", "", text.strip(), flags=re.IGNORECASE).strip()


def build_application(service: SupportService, token: str):
    _, _, Update, Application, CallbackQueryHandler, CommandHandler, _, MessageHandler, filters = _telegram()
    application = Application.builder().token(token).build()

    async def start(update: Update, context) -> None:
        del context
        if not update.effective_message:
            return
        await update.effective_message.reply_text(START_MESSAGE)

    async def private_message(update: Update, context) -> None:
        del context
        if not update.effective_message or not update.effective_chat or not update.effective_user:
            return
        text = _private_question_text(update.effective_message.text or "")
        if not text:
            await update.effective_message.reply_text("Напишите вопрос после команды /a или просто отправьте его сообщением.")
            return
        reply = service.private_question(
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
            text=text,
        )
        await _send(update.effective_message, reply)

    async def group_message(update: Update, context) -> None:
        del context
        if not update.effective_message or not update.effective_chat:
            return
        reply = service.group_message(chat_id=update.effective_chat.id)
        if reply:
            await _send(update.effective_message, reply)

    async def ask_in_group(update: Update, context) -> None:
        if not update.effective_message or not update.effective_chat or not update.effective_user:
            return
        reply = service.group_question(
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
            text=" ".join(context.args),
        )
        if reply:
            await _send(update.effective_message, reply)

    async def not_helpful(update: Update, context) -> None:
        del context
        query = update.callback_query
        if not query or not query.from_user or not query.data or not query.data.startswith("nh:"):
            return
        try:
            question_id = int(query.data.removeprefix("nh:"))
        except ValueError:
            await query.answer("Некорректная кнопка")
            return
        reply = service.not_helpful(question_id=question_id, user_id=query.from_user.id)
        await query.answer()
        if query.message:
            await _send(query.message, reply)

    async def choose_product(update: Update, context) -> None:
        del context
        query = update.callback_query
        if not query or not query.from_user or not query.data:
            return
        match = re.fullmatch(r"product:(\d+):(iikoOffice|iikoFront|iikoChain|iikoWeb|unknown)", query.data)
        if not match:
            await query.answer("Некорректная кнопка")
            return
        question_id, product = match.groups()
        reply = service.select_product(question_id=int(question_id), user_id=query.from_user.id, product=product)
        await query.answer()
        if query.message:
            await _send(query.message, reply)

    # Private answers: any plain text. Group notification: first non-command message only.
    application.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, private_message))
    application.add_handler(CommandHandler("a", ask_in_group, filters=filters.ChatType.GROUPS))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, group_message))
    application.add_handler(CallbackQueryHandler(not_helpful, pattern=r"^nh:\d+$"))
    application.add_handler(CallbackQueryHandler(choose_product, pattern=r"^product:\d+:(iikoOffice|iikoFront|iikoChain|iikoWeb|unknown)$"))
    return application


def main() -> int:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it locally.")
    database = Database(project_path("IIKO_ASSISTANT_DATABASE", "data/iiko_assistant.sqlite3"))
    calendar = BusinessCalendar.from_file(project_path("IIKO_ASSISTANT_CALENDAR", "config/production_calendar.json"))
    service = SupportService(database, KnowledgeBase(database), calendar)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs at INFO level; Telegram puts the bot token in that URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    application = build_application(service, token)
    application.run_polling(allowed_updates=["message", "callback_query"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
