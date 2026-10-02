"""Calendario di fabbrica: tre turni dal lunedì al venerdì, fermo nel fine settimana."""

import datetime as dt

SHIFTS = ((6, "Turno 1"), (14, "Turno 2"), (22, "Turno 3"))


def is_working(t: dt.datetime) -> bool:
    # Il turno 3 della domenica sera (22–24) appartiene al lunedì: semplificazione, la settimana inizia lunedì alle 00:00.
    return t.weekday() < 5


def next_working(t: dt.datetime) -> dt.datetime:
    while not is_working(t):
        t = dt.datetime.combine(t.date() + dt.timedelta(days=1), dt.time(0, 0))
    return t


def add_working_minutes(start: dt.datetime, minutes: float) -> dt.datetime:
    """Somma minuti di lavoro saltando i fine settimana."""
    t = next_working(start)
    remaining = minutes
    while remaining > 0:
        end_of_day = dt.datetime.combine(t.date() + dt.timedelta(days=1), dt.time(0, 0))
        available = (end_of_day - t).total_seconds() / 60
        if remaining <= available:
            return t + dt.timedelta(minutes=remaining)
        remaining -= available
        t = next_working(end_of_day)
    return t


def working_days_before(day: dt.date, days: int) -> dt.date:
    d = day
    while days > 0:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def shift_of(t: dt.datetime) -> str:
    h = t.hour
    if 6 <= h < 14:
        return "Turno 1"
    if 14 <= h < 22:
        return "Turno 2"
    return "Turno 3"
