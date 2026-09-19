from datetime import UTC, datetime, timedelta


SESSION_RETENTION = timedelta(days=30)


def utc_now() -> datetime:
    return datetime.now(UTC)
