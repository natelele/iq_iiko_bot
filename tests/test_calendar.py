from datetime import date, datetime, time
from pathlib import Path
from unittest import TestCase
from zoneinfo import ZoneInfo

from iiko_assistant.calendar import BusinessCalendar


MOSCOW = ZoneInfo("Europe/Moscow")


class CalendarTests(TestCase):
    def setUp(self) -> None:
        self.calendar = BusinessCalendar(non_working_dates={date(2026, 4, 6)})

    def test_working_hours_are_from_eight_to_seventeen(self) -> None:
        self.assertTrue(self.calendar.is_working_time(datetime(2026, 4, 7, 8, 0, tzinfo=MOSCOW)))
        self.assertFalse(self.calendar.is_working_time(datetime(2026, 4, 7, 17, 0, tzinfo=MOSCOW)))

    def test_fixed_holiday_is_not_a_working_day(self) -> None:
        self.assertFalse(self.calendar.is_working_day(date(2026, 2, 23)))

    def test_shortened_working_day_ends_one_hour_earlier(self) -> None:
        calendar = BusinessCalendar(shortened_working_dates={date(2026, 4, 30)})
        self.assertTrue(calendar.is_working_time(datetime(2026, 4, 30, 15, 59, tzinfo=MOSCOW)))
        self.assertFalse(calendar.is_working_time(datetime(2026, 4, 30, 16, 0, tzinfo=MOSCOW)))

    def test_2026_production_calendar_includes_off_days_and_shortened_days(self) -> None:
        root = Path(__file__).resolve().parents[1]
        calendar = BusinessCalendar.from_file(root / "config" / "production_calendar.json")
        for day in (date(2026, 1, 9), date(2026, 3, 9), date(2026, 5, 11), date(2026, 12, 31)):
            self.assertFalse(calendar.is_working_day(day))
        for day in (date(2026, 4, 30), date(2026, 5, 8), date(2026, 6, 11), date(2026, 11, 3)):
            self.assertEqual(calendar.working_end(day).strftime("%H:%M"), "16:00")

    def test_next_opening_skips_weekend_and_configured_day_off(self) -> None:
        opening = self.calendar.next_work_start(datetime(2026, 4, 3, 18, 0, tzinfo=MOSCOW))
        self.assertEqual(opening, datetime(2026, 4, 7, 8, 0, tzinfo=MOSCOW))

    def test_period_key_is_shared_for_one_off_hours_interval(self) -> None:
        friday = datetime(2026, 4, 3, 18, 0, tzinfo=MOSCOW)
        sunday = datetime(2026, 4, 5, 11, 0, tzinfo=MOSCOW)
        self.assertEqual(self.calendar.off_hours_period_key(friday), self.calendar.off_hours_period_key(sunday))
