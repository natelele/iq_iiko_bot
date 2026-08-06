from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import replace

from .database import Database
from .iikohelp import secure_https_handler
from .models import Answer, Article, ArticleCandidate


logger = logging.getLogger(__name__)


UNKNOWN_ANSWER = (
    "В подтверждённой базе знаний нет достаточно релевантного ответа на этот вопрос. "
    "Уточните, в каком продукте iiko вы работаете и на каком шаге возникла проблема."
)

CLARIFY_PRODUCT_ANSWER = (
    "Чтобы подсказать точнее, выберите продукт iiko:"
)


def _excerpt(text: str, limit: int = 700) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    boundary = cleaned.rfind(". ", 0, limit)
    return cleaned[: boundary + 1 if boundary > 80 else limit].rstrip() + "…"


def _product_family(source_url: str) -> str:
    fragment = source_url.split("#!", 1)[-1].split("/", 1)[0].lower()
    return re.sub(r"-\d+-(?:\d+|x)$", "", fragment)


def _requested_products(question: str) -> set[str]:
    normalized = question.lower().replace(" ", "")
    return set(re.findall(r"iiko(?:office|front|chain|delivery|web|card|callcenter|franchise)", normalized))


def _inferred_products(question: str) -> set[str]:
    """Infer a product only from terms that are unlikely to belong to another iiko product."""
    normalized = question.lower()
    hints = {
        "iikofront": ("терминал", "кассов", "кассир", "фискал", "пречек", "официант", "бармен"),
        "iikochain": ("прейскурант", "кофейн", "франчайз", "концепц"),
        "iikoweb": ("личный кабинет", "веб-кабинет", "веб кабинет"),
    }
    matches = {product for product, words in hints.items() if any(word in normalized for word in words)}
    return matches if len(matches) == 1 else set()


def _is_general_question(question: str) -> bool:
    """Questions about an installation or a licence can be useful without a help-centre article."""
    normalized = question.lower()
    general_signals = (
        "можно ли", "нужно ли", "сколько", "лицензи", "рабочих мест", "рабочее место",
        "компьютер", "установить", "ккт", "оборудован",
    )
    procedure_signals = (
        "как ", "хочу ", "закры", "откры", "созда", "настро", "провест", "напечат",
    )
    return any(signal in normalized for signal in general_signals) and not any(
        signal in normalized for signal in procedure_signals
    )


def _is_technical_question(question: str) -> bool:
    return bool(re.search(r"\b(api|апи|интеграц|разработ|webhook|вебхук|sdk)\b", question, re.IGNORECASE))


def _is_technical_article(article: Article) -> bool:
    source = article.source_url.lower()
    return any(
        marker in source
        for marker in ("releasenotes", "api-documentations", "changelog", "cloud-api", "iikocloudapi", "api-")
    )


def _instruction_bonus(question: str, article: Article) -> float:
    """Promote a step-by-step guide when the user asks how to perform an action."""
    question_lower = question.lower()
    text = article.plain_text.lower()
    bonus = 0.0
    asks_to_create_guest = bool(re.search(r"(?:созд|добав|регистр)\w*.*гост", question_lower))
    direct_guest_guide = bool(re.search(
        r"как\s+(?:созд|добав|регистр)\w*(?:\s+\w+){0,3}\s+гост\w*",
        text,
    ))
    guest_registration_guide = bool(re.search(
        r"(?:регистрац|создани)\w*(?:\s+\w+){0,3}\s+гост\w*",
        text,
    ))
    if asks_to_create_guest and direct_guest_guide:
        bonus += 100.0
    elif asks_to_create_guest and guest_registration_guide:
        bonus += 80.0
    asks_to_select_cashier = bool(re.search(r"(?:выб|сменить)\w*.*кассир", question_lower))
    direct_cashier_guide = bool(re.search(
        r"выб\w*(?:\s+\w+){0,4}\s+в\s+качестве\s+кассир\w*"
        r"|для\s+выбор\w*\s+кассир\w*"
        r"|сменить\s+кассир\w*",
        text,
    ))
    if asks_to_select_cashier and direct_cashier_guide:
        bonus += 100.0
    asks_for_external_menu = "внешн" in question_lower and "меню" in question_lower
    if asks_for_external_menu and "external-menu" in article.source_url.lower():
        bonus += 90.0
    if re.search(r"\bкак\b|\bхочу\b", question_lower) and re.search(
        r"\b(?:шаг|нажмите|выберите|перейдите)\b", text,
    ):
        bonus += 4.0
    return bonus


def _prioritize_candidates(question: str, candidates: list[ArticleCandidate]) -> list[ArticleCandidate]:
    """Keep end-user help above release notes and developer documentation by default."""
    if not _is_technical_question(question):
        end_user_candidates = [candidate for candidate in candidates if not _is_technical_article(candidate.article)]
        if end_user_candidates:
            candidates = end_user_candidates
    return sorted(
        (replace(candidate, score=candidate.score + _instruction_bonus(question, candidate.article)) for candidate in candidates),
        key=lambda candidate: -candidate.score,
    )


def _normalize_question(question: str) -> str:
    return re.sub(r"\bайко\b", "iiko", question, flags=re.IGNORECASE)


def _diverse_candidates(candidates: list[ArticleCandidate], limit: int = 3) -> list[ArticleCandidate]:
    """Avoid sending three near-identical versions of the same product to the model."""
    selected: list[ArticleCandidate] = []
    used_families: set[str] = set()
    for candidate in candidates:
        family = _product_family(candidate.article.source_url)
        if family not in used_families:
            selected.append(candidate)
            used_families.add(family)
        if len(selected) == limit:
            return selected
    for candidate in candidates:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) == limit:
            break
    return selected


def _relevant_excerpt(article: Article, terms: tuple[str, ...], limit: int = 1_400) -> str:
    """Return the most keyword-dense local passage, not always the start of a long article."""
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", article.plain_text).strip())
    if not sentences:
        return ""
    roots = tuple(term[: max(4, len(term) - 3)] for term in terms)
    guest_creation_request = "гост" in roots and any(root.startswith(("созд", "добав", "регистр")) for root in roots)
    windows: list[tuple[int, int]] = []
    for start in range(len(sentences)):
        window = " ".join(sentences[start:start + 5]).lower()
        score = sum(window.count(root) for root in roots)
        if guest_creation_request and re.search(
            r"(?:регистрац|как\s+добав|новый\s+гост)(?:\s+\w+){0,5}", window,
        ):
            score += 12
        if score:
            windows.append((score, start))
    if not windows:
        return _excerpt(article.plain_text, limit)
    _, start = max(windows, key=lambda item: (item[0], -item[1]))
    return _excerpt(" ".join(sentences[start:start + 7]), limit)


def _json_object(value: str) -> dict[str, object] | None:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


class KnowledgeBase:
    """Retrieval-first answers with an optional strictly grounded DeepSeek summary."""

    def __init__(
        self,
        database: Database,
        *,
        api_key: str | None = None,
        model: str | None = None,
        summary_provider: Callable[[str], str] | None = None,
    ) -> None:
        self.database = database
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.summary_provider = summary_provider

    def answer(
        self, question: str, *, product: str | None = None, accept_ambiguous_product: bool = False
    ) -> Answer:
        question = _normalize_question(question)
        candidates = _prioritize_candidates(question, self.database.search_article_candidates(question, limit=120))
        model_is_available = bool(self.summary_provider or self.api_key)
        if not candidates or not self._has_grounded_match(candidates[0]):
            general = (
                self._general_deepseek_answer(question)
                if model_is_available and _is_general_question(question)
                else None
            )
            return general or Answer(UNKNOWN_ANSWER, None, False)

        requested_products = _requested_products(question)
        if product:
            requested_products.add(product.lower())
        if not requested_products:
            requested_products = _inferred_products(question)
        if requested_products:
            product_candidates = [
                candidate
                for candidate in candidates
                if any(product in _product_family(candidate.article.source_url) for product in requested_products)
            ]
            if product_candidates:
                candidates = product_candidates
        is_ambiguous = not requested_products and self._is_product_ambiguous(candidates)
        if is_ambiguous and not accept_ambiguous_product:
            return Answer(CLARIFY_PRODUCT_ANSWER, None, False, product_options=True)

        if model_is_available:
            if accept_ambiguous_product:
                # "Не знаю" means choose the best user-facing guide, not a random product-specific alternative.
                selected_candidates = candidates[:1]
            else:
                selected_candidates = candidates[:5] if requested_products else _diverse_candidates(candidates)
            result = self._answer_with_deepseek(
                question,
                selected_candidates,
                product_is_specified=bool(requested_products),
                product_is_unknown=accept_ambiguous_product,
            )
            if result is not None:
                return result
        best = candidates[0]
        return Answer(_relevant_excerpt(best.article, best.matched_terms), best.article, True)

    @staticmethod
    def _has_grounded_match(candidate: ArticleCandidate) -> bool:
        if candidate.coverage < 0.5:
            return False
        return candidate.distinctive_term_count == 0 or candidate.distinctive_coverage >= 0.6

    @staticmethod
    def _is_product_ambiguous(candidates: list[ArticleCandidate]) -> bool:
        best = candidates[0]
        close_families = {
            _product_family(candidate.article.source_url)
            for candidate in candidates[:24]
            if _product_family(candidate.article.source_url) in {"iikooffice", "iikofront", "iikochain", "iikoweb"}
            if candidate.coverage == best.coverage and candidate.score >= best.score * 0.85
        }
        return len(close_families) > 1

    def _answer_with_deepseek(
        self,
        question: str,
        candidates: list[ArticleCandidate],
        *,
        product_is_specified: bool,
        product_is_unknown: bool,
    ) -> Answer | None:
        contexts = "\n\n".join(
            f"[{candidate.article.id}] {candidate.article.source_url}\n"
            f"{_relevant_excerpt(candidate.article, candidate.matched_terms, 450)}"
            for candidate in candidates
        )
        if product_is_specified:
            clarification_instruction = (
                "Продукт уже указан в вопросе: не проси продукт или версию. Если требуется уточнение, "
                "спроси только о действии или типе позиции.\n\n"
            )
        elif product_is_unknown:
            clarification_instruction = (
                "Клиент не знает продукт iiko. Не проси его выбрать продукт: дай самый универсальный "
                "практический ответ по подходящей статье.\n\n"
            )
        else:
            clarification_instruction = "Если без продукта нельзя выбрать одну статью, вежливо попроси назвать только продукт iiko.\n\n"
        prompt = (
            "Ты помощник проектного отдела iiko. Выбери только один наиболее релевантный фрагмент "
            "из КАНДИДАТОВ. Не используй знания вне кандидатов. Не упоминай номера статей, кандидатов "
            "или процесс поиска: пользователь увидит кнопку со ссылкой отдельно. "
            "Ответь по-русски, максимум тремя предложениями. Верни СТРОГО JSON без Markdown: "
            "{\"article_id\": число, \"answer\": \"текст\"}. Всегда укажи id выбранной статьи. "
            + clarification_instruction
            + f"ВОПРОС: {question}\n\nКАНДИДАТЫ:\n{contexts}"
        )
        raw_answer = self._model_response(prompt)
        if raw_answer is None:
            return None
        payload = _json_object(raw_answer)
        if payload is None or not isinstance(payload.get("answer"), str):
            return None
        answer = payload["answer"].strip()
        selected_id = payload.get("article_id")
        if not isinstance(selected_id, int) or not answer:
            return None
        selected = next((candidate for candidate in candidates if candidate.article.id == selected_id), None)
        return Answer(answer, selected.article, True) if selected else None

    def _general_deepseek_answer(self, question: str) -> Answer | None:
        prompt = (
            "Ты опытный консультант iiko. Кратко ответь по-русски на вопрос. Если ответ зависит от "
            "лицензии, редакции или схемы установки, объясни общий принцип и прямо укажи, что именно "
            "нужно уточнить. Не придумывай точные условия лицензии.\n\n"
            + f"ВОПРОС: {question}"
        )
        raw_answer = self._model_response(prompt)
        return Answer(raw_answer, None, False) if raw_answer else None

    def _model_response(self, prompt: str) -> str | None:
        try:
            if self.summary_provider:
                return self.summary_provider(prompt).strip()
            if self.api_key:
                return self._deepseek(prompt)
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as error:
            logger.warning("DeepSeek answer unavailable: %s", type(error).__name__)
        return None

    def _deepseek(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Соблюдай ограничения и формат из пользовательского промпта."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                # The model may spend part of this budget on hidden reasoning.
                # The prompt itself is kept compact, but 1,200 leaves room for JSON output.
                "max_tokens": 1200,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(secure_https_handler())
        with opener.open(request, timeout=12) as response:  # nosec B310: fixed HTTPS endpoint
            data = json.loads(response.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"]).strip()
