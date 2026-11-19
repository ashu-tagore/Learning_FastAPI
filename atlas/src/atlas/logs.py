"""Logs as JSON lines, each carrying the id of the request it belongs to (lesson 4.15).

One JSON object per line lets log tools filter by field ("all lines for request X",
"requests slower than 500 ms") instead of searching free text.
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Set by the API's middleware for each request. A ContextVar is per request even when many
# requests run at once on one event loop, so code deep inside a request never has to pass
# the id along: every log line made while handling it picks the id up automatically.
current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

# Attributes every log record has; anything else on a record came from extra={...}.
_STANDARD_ATTRIBUTES = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRIBUTES}
        line.update(extras)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Send Atlas's logs to stderr as JSON lines. Safe to call more than once."""
    logger = logging.getLogger("atlas")
    logger.setLevel(level)
    is_configured = any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers)
    if is_configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    # A record factory reaches every logger, including child loggers like "atlas.api"
    # and any handler (pytest's included), which a filter on one logger wouldn't.
    previous_factory = logging.getLogRecordFactory()

    def record_with_request_id(*args: Any, **kwargs: Any) -> logging.LogRecord:
        # Runs when the record is made, while the request's context is still current.
        record = previous_factory(*args, **kwargs)
        record.request_id = current_request_id.get()
        return record

    logging.setLogRecordFactory(record_with_request_id)
