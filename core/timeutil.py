"""Time helpers. Every timestamp in this project is Jakarta time (GMT+7) —
it's an Indonesia-focused dataset and every reader of these tables is in that
timezone.

Unlike a naive-datetime approach, timestamps are stored as ISO-8601 strings
*with* the +07:00 offset. SQLite has no timestamp type, and an explicit offset
keeps the values unambiguous if the data is later moved to PostgreSQL (the
portable-SQL rule in core/db.py)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

JAKARTA = timezone(timedelta(hours=7))


def now_jakarta() -> datetime:
    """Current time as an aware datetime in Jakarta time."""
    return datetime.now(JAKARTA)


def now_iso() -> str:
    """Current Jakarta time as ISO-8601 with offset, second precision."""
    return now_jakarta().isoformat(timespec="seconds")


def today_jakarta() -> date:
    return now_jakarta().date()


def to_iso(value: str | datetime | date | None) -> str | None:
    """Normalise a datetime/date/ISO string from a source into our stored
    form. Naive datetimes are assumed to already be Jakarta time; aware ones
    are converted. A bare date stays a bare date (YYYY-MM-DD) — job sites
    often publish only the day, and inventing midnight would be false
    precision."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=JAKARTA)
        return dt.astimezone(JAKARTA).isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) == 10:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return to_iso(dt)


def from_epoch(epoch_seconds: int | float) -> str:
    """A Unix epoch (always UTC) as Jakarta ISO-8601."""
    return datetime.fromtimestamp(float(epoch_seconds), tz=UTC).astimezone(JAKARTA).isoformat(timespec="seconds")


def days_ago(iso_value: str | None, today: date | None = None) -> int | None:
    """Whole days between an ISO date/datetime and today (Jakarta). None if
    the value can't be parsed. Used for the 60-day collection window."""
    if not iso_value:
        return None
    try:
        day = date.fromisoformat(iso_value[:10])
    except ValueError:
        return None
    return ((today or today_jakarta()) - day).days
