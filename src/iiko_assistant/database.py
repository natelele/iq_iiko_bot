from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import Article, ArticleCandidate, AskedQuestion, FeedbackResult


_RUSSIAN_STOP_WORDS = frozenset({
    "а", "без", "был", "быть", "в", "во", "вот", "все", "вы", "где", "да", "для", "до", "его",
    "ее", "если", "же", "за", "и", "из", "или", "как", "когда", "кто", "ли", "мне", "мы", "на",
    "над", "не", "нет", "но", "о", "об", "он", "она", "они", "от", "по", "под", "пожалуйста", "при",
    "про", "программа", "программе", "программу", "программ", "привет", "помоги", "помогите",
    "хочу", "хотел", "хотела", "нужно", "нужен", "нужна", "надо",
    "с", "со", "так", "то", "у", "уже", "что", "это", "этот", "я", "здравствуйте",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plain_text(markdown: str) -> str:
    without_code = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)
    without_markup = re.sub(r"!?(?:\[[^\]]*\])\([^)]*\)", " ", without_code)
    without_symbols = re.sub(r"[#>*_`~|-]", " ", without_markup)
    return re.sub(r"\s+", " ", without_symbols).strip()


def _search_terms(query: str) -> tuple[str, ...]:
    result: list[str] = []
    for token in re.findall(r"\w+", query.lower(), flags=re.UNICODE):
        if len(token) < 3 or token in _RUSSIAN_STOP_WORDS or token in result:
            continue
        result.append(token)
    return tuple(result)


def _term_root(token: str) -> str:
    """A small Russian stemmer for retrieval, without an extra runtime dependency."""
    if not re.fullmatch(r"[а-яё]+", token, flags=re.IGNORECASE):
        return token
    for ending in (
        "иями", "ями", "ами", "ости", "ость", "ение", "ения", "ений", "ировать", "ировать",
        "ировать", "ироваться", "ироваться", "иться", "аться", "яться", "овать", "евать",
        "ить", "ать", "ять", "еть", "уть", "ешь", "ете", "ем", "ют", "ут", "ит", "ат",
        "ят", "ого", "ему", "ыми", "ими", "ому", "ая", "яя", "ую", "юю", "ов", "ев",
        "ах", "ях", "ам", "ям", "ом", "ем", "ой", "ей", "ы", "и", "а", "я", "у", "ю",
        "е", "о", "ь",
    ):
        if token.endswith(ending) and len(token) - len(ending) >= 4:
            return token[: -len(ending)]
    return token


def _canonical_article_key(source_url: str) -> str:
    parsed = re.sub(r"-\d+-(?:\d+|x)(?=/|$)", "", source_url)
    return parsed


def _article_version(source_url: str) -> tuple[int, int]:
    match = re.search(r"-(\d+)-(\d+|x)(?=/|$)", source_url)
    if not match:
        return 0, 0
    return int(match.group(1)), 999 if match.group(2) == "x" else int(match.group(2))


class Database:
    """SQLite persistence. It contains no messenger-specific data structures."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY,
                source_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                markdown TEXT NOT NULL,
                plain_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5(
                article_id UNINDEXED, title, content, tokenize='unicode61'
            );
            CREATE TABLE IF NOT EXISTS group_notifications (
                chat_id INTEGER NOT NULL,
                period_key TEXT NOT NULL,
                notified_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, period_key)
            );
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                is_group INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                article_id INTEGER REFERENCES articles(id),
                source_url TEXT,
                feedback_count INTEGER NOT NULL DEFAULT 0,
                ticket_id INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY,
                question_id INTEGER NOT NULL REFERENCES questions(id),
                user_id INTEGER NOT NULL,
                stage INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(question_id, user_id, stage)
            );
            CREATE TABLE IF NOT EXISTS support_requests (
                id INTEGER PRIMARY KEY,
                question_id INTEGER NOT NULL UNIQUE REFERENCES questions(id),
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                created_at TEXT NOT NULL,
                external_reference TEXT
            );
            """
        )
        self.connection.commit()

    @staticmethod
    def _article_from_row(row: sqlite3.Row) -> Article:
        return Article(
            id=row["id"], source_url=row["source_url"], title=row["title"],
            markdown=row["markdown"], plain_text=row["plain_text"], updated_at=row["updated_at"],
        )

    def upsert_article(self, source_url: str, title: str, markdown: str) -> Article:
        plain_text = _plain_text(markdown)
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        now = _now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO articles (source_url, title, markdown, plain_text, content_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_url) DO UPDATE SET title=excluded.title, markdown=excluded.markdown,
                    plain_text=excluded.plain_text, content_hash=excluded.content_hash, updated_at=excluded.updated_at""",
                (source_url, title, markdown, plain_text, content_hash, now),
            )
            row = self.connection.execute(
                "SELECT * FROM articles WHERE source_url = ?", (source_url,)
            ).fetchone()
            self.connection.execute("DELETE FROM article_fts WHERE article_id = ?", (row["id"],))
            self.connection.execute(
                "INSERT INTO article_fts (article_id, title, content) VALUES (?, ?, ?)",
                (row["id"], title, plain_text),
            )
        return self._article_from_row(row)

    def search_article_candidates(self, query: str, limit: int = 24) -> list[ArticleCandidate]:
        terms = _search_terms(query)
        if not terms:
            return []
        roots = tuple(_term_root(term) for term in terms)
        fts_query = " OR ".join(f"{root}*" for root in roots)
        rows = self.connection.execute(
            """SELECT a.* FROM article_fts f
            JOIN articles a ON a.id = f.article_id
            WHERE article_fts MATCH ?""",
            (fts_query,),
        ).fetchall()
        if not rows:
            rows = self.connection.execute("SELECT * FROM articles").fetchall()

        document_count = len(rows)
        document_frequency = {
            root: sum(root in row["title"].lower() or root in row["plain_text"].lower() for row in rows)
            for root in roots
        }
        distinctive_roots = {
            root for root, frequency in document_frequency.items() if frequency / document_count <= 0.25
        }
        ranked: list[ArticleCandidate] = []
        for row in rows:
            title = row["title"].lower()
            content = row["plain_text"].lower()
            matched = tuple(term for term, root in zip(terms, roots) if root in title or root in content)
            if not matched:
                continue
            coverage = len(matched) / len(terms)
            score = coverage * 20
            for root in roots:
                inverse_frequency = math.log((document_count + 1) / (document_frequency[root] + 1)) + 1
                title_hits = min(title.count(root), 2)
                content_hits = min(content.count(root), 4)
                score += (title_hits * 5 + content_hits * 1.5) * inverse_frequency
            if coverage == 1 and len(terms) > 1:
                score += 8
            distinctive_match_count = sum(
                root in distinctive_roots and (root in title or root in content) for root in roots
            )
            ranked.append(
                ArticleCandidate(
                    self._article_from_row(row), score, matched, len(terms),
                    distinctive_match_count, len(distinctive_roots),
                )
            )
        ranked.sort(
            key=lambda candidate: (
                -candidate.score,
                -_article_version(candidate.article.source_url)[0],
                -_article_version(candidate.article.source_url)[1],
                candidate.article.id,
            )
        )
        unique: dict[str, ArticleCandidate] = {}
        for candidate in ranked:
            key = _canonical_article_key(candidate.article.source_url)
            if key not in unique:
                unique[key] = candidate
        return list(unique.values())[:limit]

    def search_articles(self, query: str, limit: int = 5) -> list[Article]:
        return [candidate.article for candidate in self.search_article_candidates(query, limit)]

    def list_articles(self) -> list[Article]:
        rows = self.connection.execute("SELECT * FROM articles ORDER BY id").fetchall()
        return [self._article_from_row(row) for row in rows]

    def article_source_urls(self) -> set[str]:
        rows = self.connection.execute("SELECT source_url FROM articles").fetchall()
        return {row["source_url"] for row in rows}

    def mark_group_notified(self, chat_id: int, period_key: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO group_notifications (chat_id, period_key, notified_at)
                VALUES (?, ?, ?)""",
                (chat_id, period_key, _now()),
            )
        return cursor.rowcount == 1

    def create_question(
        self, *, chat_id: int, user_id: int, is_group: bool, question: str,
        answer: str, article_id: int | None, source_url: str | None,
    ) -> AskedQuestion:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO questions
                (chat_id, user_id, is_group, question, answer, article_id, source_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, user_id, int(is_group), question, answer, article_id, source_url, _now()),
            )
        return AskedQuestion(cursor.lastrowid, chat_id, user_id, is_group, question, article_id, source_url)

    def get_question(self, question_id: int) -> AskedQuestion | None:
        row = self.connection.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        if row is None:
            return None
        return AskedQuestion(
            row["id"], row["chat_id"], row["user_id"], bool(row["is_group"]),
            row["question"], row["article_id"], row["source_url"],
        )

    @contextmanager
    def _immediate_transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def register_not_helpful(self, question_id: int, user_id: int) -> FeedbackResult:
        with self._immediate_transaction():
            question = self.connection.execute(
                "SELECT * FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
            if question is None:
                return FeedbackResult("not_found")
            if question["user_id"] != user_id:
                return FeedbackResult("not_owner")
            if question["ticket_id"] is not None:
                return FeedbackResult("already_escalated", question["ticket_id"])

            stage = question["feedback_count"] + 1
            self.connection.execute(
                "INSERT INTO feedback_events (question_id, user_id, stage, created_at) VALUES (?, ?, ?, ?)",
                (question_id, user_id, stage, _now()),
            )
            self.connection.execute(
                "UPDATE questions SET feedback_count = ? WHERE id = ?", (stage, question_id)
            )
            cursor = self.connection.execute(
                """INSERT INTO support_requests (question_id, chat_id, user_id, question, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (question_id, question["chat_id"], user_id, question["question"], _now()),
            )
            ticket_number = cursor.lastrowid
            self.connection.execute(
                "UPDATE questions SET ticket_id = ? WHERE id = ?", (ticket_number, question_id)
            )
            return FeedbackResult("ticket_created", ticket_number)

    def get_question_ticket(self, question_id: int) -> int | None:
        row = self.connection.execute("SELECT ticket_id FROM questions WHERE id = ?", (question_id,)).fetchone()
        return None if row is None else row["ticket_id"]
