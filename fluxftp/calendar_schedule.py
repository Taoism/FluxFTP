"""Calendar recurrence in local wall time; missed runs coalesce."""
from datetime import datetime, timedelta
import calendar

MODES = ('Interval', 'Daily', 'Weekly', 'Monthly')


def next_calendar(due, now, mode, anchor_day=0, wall_time=''):
    start = datetime.fromtimestamp(due)
    if wall_time:
        hour, minute, second = map(int, wall_time.split(':'))
        start = start.replace(hour=hour, minute=minute, second=second)
    today = datetime.fromtimestamp(max(now, due))
    if mode == 'Monthly':
        day = anchor_day or start.day
        year, month = today.year, today.month
        while True:
            candidate = start.replace(year=year, month=month, day=min(day, calendar.monthrange(year, month)[1]))
            stamp = candidate.timestamp()
            if stamp > max(now, due):
                return stamp
            month += 1
            if month == 13:
                year, month = year + 1, 1
    candidate = today.replace(hour=start.hour, minute=start.minute, second=start.second, microsecond=0, fold=0)
    if mode == 'Weekly':
        candidate += timedelta(days=(start.weekday() - candidate.weekday()) % 7)
    if candidate.date() == start.date() or candidate.timestamp() <= max(now, due):
        candidate += timedelta(days=7 if mode == 'Weekly' else 1)
    return candidate.timestamp()
