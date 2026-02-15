from __future__ import annotations

from datetime import date, timedelta


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    current = first_next - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_equity_market_holidays(year: int) -> set[date]:
    holidays: set[date] = set()

    holidays.add(_observed_fixed_holiday(year, 1, 1))  # New Year's Day
    holidays.add(_nth_weekday(year, 1, 0, 3))  # MLK Day
    holidays.add(_nth_weekday(year, 2, 0, 3))  # Presidents' Day

    good_friday = _easter_sunday(year) - timedelta(days=2)
    holidays.add(good_friday)

    holidays.add(_last_weekday(year, 5, 0))  # Memorial Day
    holidays.add(_observed_fixed_holiday(year, 6, 19))  # Juneteenth
    holidays.add(_observed_fixed_holiday(year, 7, 4))  # Independence Day
    holidays.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    holidays.add(_observed_fixed_holiday(year, 12, 25))  # Christmas

    return holidays


def is_us_equity_market_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False

    holidays = set()
    for year in (day.year - 1, day.year, day.year + 1):
        holidays.update(us_equity_market_holidays(year))

    return day not in holidays
