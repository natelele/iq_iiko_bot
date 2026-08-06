from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


MOSCOW = ZoneInfo("Europe/Moscow")

_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
_MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _parse_dates(values: list[str]) -> set[date]:
    return {date.fromisoformat(value) for value in values}


@dataclass(frozen=True)
class BusinessCalendar:
    """Moscow working-time calendar with explicit Russian holiday overrides.

    National fixed holidays are included. Officially transferred days must be
    added to the JSON configuration for every production year.
    """

    start: time = time(8, 0)
    end: time = time(17, 0)
    non_working_dates: set[date] = field(default_factory=set)
    working_dates: set[date] = field(default_factory=set)
    shortened_working_dates: set[date] = field(default_factory=set)
    timezone: ZoneInfo = MOSCOW

    @classmethod
    def from_file(cls, path: str | Path | None) -> "BusinessCalendar":
        if not path:
            return cls()
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        hours = payload.get("working_hours", {})
        return cls(
            start=_parse_time(hours.get("start", "08:00")),
            end=_parse_time(hours.get("end", "17:00")),
            non_working_dates=_parse_dates(payload.get("non_working_dates", [])),
            working_dates=_parse_dates(payload.get("working_dates", [])),
            shortened_working_dates=_parse_dates(payload.get("shortened_working_dates", [])),
            timezone=ZoneInfo(payload.get("timezone", "Europe/Moscow")),
        )

    @staticmethod
    def _fixed_public_holidays(day: date) -> bool:
        return (day.month == 1 and 1 <= day.day <= 8) or (day.month, day.day) in {
            (2, 23),
            (3, 8),
            (5, 1),
            (5, 9),
            (6, 12),
            (11, 4),
        }

    def is_working_day(self, day: date) -> bool:
        if day in self.working_dates:
            return True
        if day in self.non_working_dates:
            return False
        return day.weekday() < 5 and not self._fixed_public_holidays(day)

    def working_end(self, day: date) -> time:
        """Return the end of the working day, one hour earlier before a holiday."""
        if day not in self.shortened_working_dates:
            return self.end
        return (datetime.combine(day, self.end) - timedelta(hours=1)).time()

    def localize(self, moment: datetime | None = None) -> datetime:
        if moment is None:
            return datetime.now(self.timezone)
        if moment.tzinfo is None:
            return moment.replace(tzinfo=self.timezone)
        return moment.astimezone(self.timezone)

    def is_working_time(self, moment: datetime | None = None) -> bool:
        local = self.localize(moment)
        return self.is_working_day(local.date()) and self.start <= local.time() < self.working_end(local.date())

    def next_work_start(self, moment: datetime | None = None) -> datetime:
        local = self.localize(moment)
        candidate_day = local.date()
        if self.is_working_day(candidate_day) and local.time() < self.start:
            return datetime.combine(candidate_day, self.start, self.timezone)
        if self.is_working_time(local):
            return local
        candidate_day += timedelta(days=1)
        while not self.is_working_day(candidate_day):
            candidate_day += timedelta(days=1)
        return datetime.combine(candidate_day, self.start, self.timezone)

    def off_hours_period_key(self, moment: datetime | None = None) -> str:
        """The next opening uniquely identifies a continuous off-hours period."""
        return self.next_work_start(moment).isoformat()

    def format_next_work_start(self, moment: datetime | None = None) -> str:
        opening = self.next_work_start(moment)
        return (
            f"{_WEEKDAYS_RU[opening.weekday()]}, {opening.day} "
            f"{_MONTHS_RU[opening.month - 1]}, {opening:%H:%M}"
        )
