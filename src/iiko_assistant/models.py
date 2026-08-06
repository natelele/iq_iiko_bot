from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Article:
    id: int
    source_url: str
    title: str
    markdown: str
    plain_text: str
    updated_at: str


@dataclass(frozen=True)
class ArticleCandidate:
    article: Article
    score: float
    matched_terms: tuple[str, ...]
    term_count: int
    distinctive_match_count: int
    distinctive_term_count: int

    @property
    def coverage(self) -> float:
        return len(self.matched_terms) / self.term_count if self.term_count else 0.0

    @property
    def distinctive_coverage(self) -> float:
        return (
            self.distinctive_match_count / self.distinctive_term_count
            if self.distinctive_term_count else 0.0
        )


@dataclass(frozen=True)
class Answer:
    text: str
    article: Article | None
    found: bool
    product_options: bool = False


@dataclass(frozen=True)
class AskedQuestion:
    id: int
    chat_id: int
    user_id: int
    is_group: bool
    question: str
    article_id: int | None
    source_url: str | None


@dataclass(frozen=True)
class FeedbackResult:
    kind: Literal["not_owner", "ticket_created", "already_escalated", "not_found"]
    ticket_number: int | None = None
