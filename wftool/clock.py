"""Single source of the current time, so tests can control it."""

from datetime import datetime, timezone

_override = None
_offset = None  # test mode only: real time shifted by this timedelta


def now():
    """Current time as an aware UTC datetime."""
    if _override is not None:
        return _override
    real = datetime.now(timezone.utc)
    return real + _offset if _offset is not None else real


def now_iso():
    """Current time as ISO 8601 text in UTC, second precision."""
    return now().replace(microsecond=0).isoformat()


def set_now(value):
    """Freeze the clock (tests). Pass None to return to real time."""
    global _override
    _override = value


def set_offset(value):
    """Shift real time by a timedelta (the app's test mode only). Pass None to return to real time."""
    global _offset
    _offset = value
