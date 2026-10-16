"""Bounded NYSE core-session calendar, verified 2026-10-16.

Source: https://www.nyse.com/trade/hours-calendars (2026–2028).
Fail closed outside this horizon. Unscheduled closures require a code update.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HOLIDAYS = set(
    """
2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25
2026-06-19 2026-07-03 2026-09-07 2026-11-26 2026-12-25
2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31
2027-06-18 2027-07-05 2027-09-06 2027-11-25 2027-12-24
2028-01-17 2028-02-21 2028-04-14 2028-05-29 2028-06-19
2028-07-04 2028-09-04 2028-11-23 2028-12-25
""".split()
)
EARLY = {"2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24"}


def is_session(day: date) -> bool:
    if day.year not in (2026, 2027, 2028):
        raise ValueError("Calendrier hors périmètre validé 2026–2028")
    return day.weekday() < 5 and day.isoformat() not in HOLIDAYS


def next_session(day: date) -> date:
    day += timedelta(days=1)
    while not is_session(day):
        day += timedelta(days=1)
    return day


def bounds(day: date) -> tuple[datetime, datetime]:
    if not is_session(day):
        raise ValueError(f"{day} n'est pas une séance US")
    hour = 13 if day.isoformat() in EARLY else 16
    return (
        datetime.combine(day, time(9, 30), NY).astimezone(UTC),
        datetime.combine(day, time(hour), NY).astimezone(UTC),
    )
