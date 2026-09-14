"""Structured stderr logging with request-scoped context and safe error details."""

import asyncio
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from traceback import extract_tb
from typing import Any

from dotenv import load_dotenv

load_dotenv()

_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})


def get_log_context() -> dict[str, Any]:
    return _context.get().copy()


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.event_fields = {**_context.get(), **getattr(record, "event_fields", {})}
        return True


class EventFormatter(logging.Formatter):
    """Never include exception messages, source text or locals in tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        fields = dict(getattr(record, "event_fields", {}))
        if record.exc_info and record.exc_info[0]:
            fields["error_type"] = record.exc_info[0].__name__
            fields["traceback"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in extract_tb(record.exc_info[2])[-8:]
            ]
        event = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            **fields,
        }
        log_format = os.getenv("LOG_FORMAT", "json")
        if log_format == "json":
            return json.dumps(event, default=str, ensure_ascii=True)
        # Keep custom legacy LOG_FORMAT strings usable while including context.
        fmt = (
            "%(asctime)s %(levelname)s %(name)s %(message)s"
            if log_format == "text" else log_format
        )
        safe_record = logging.makeLogRecord(record.__dict__.copy())
        safe_record.exc_info = None
        safe_record.exc_text = None
        safe_record.stack_info = None
        return f"{logging.Formatter(fmt).format(safe_record)} {json.dumps(fields, default=str)}"


def get_logger(name: str = __name__) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
    if not any(isinstance(item, ContextFilter) for item in logger.filters):
        logger.addFilter(ContextFilter())
    if not logger.handlers:
        handler = logging.StreamHandler()  # stderr: stdout belongs to MCP JSON-RPC.
        handler.setFormatter(EventFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger


@contextmanager
def log_operation(
    logger: logging.Logger,
    event: str,
    *,
    expected_errors: tuple[type[Exception], ...] = (),
    **fields: Any,
) -> Iterator[dict[str, Any]]:
    """Log an operation's duration and outcome without its input or output text."""
    started = perf_counter()
    with log_context(**fields):
        logger.info(f"{event}.started")
        result: dict[str, Any] = {}
        try:
            yield result
        except asyncio.CancelledError:
            result["outcome"] = "cancelled"
            logger.warning(
                f"{event}.cancelled",
                extra={"event_fields": {
                    **result, "duration_ms": round((perf_counter() - started) * 1000, 2),
                }},
            )
            raise
        except Exception as error:
            expected = isinstance(error, expected_errors) or result.get("outcome") == "rejected"
            result.setdefault("outcome", "rejected" if expected else "failed")
            logger.log(
                logging.WARNING if expected else logging.ERROR,
                f"{event}.failed", exc_info=not expected,
                extra={"event_fields": {
                    **result, "error_type": type(error).__name__,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                }},
            )
            raise
        else:
            failed = result.get("status_code", 200) >= 400
            result.setdefault("outcome", "failed" if failed else "succeeded")
            level = logging.ERROR if result.get("status_code", 200) >= 500 else (
                logging.WARNING if failed else logging.INFO
            )
            logger.log(
                level, f"{event}.completed",
                extra={"event_fields": {
                    **result, "duration_ms": round((perf_counter() - started) * 1000, 2),
                }},
            )
