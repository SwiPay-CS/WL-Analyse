"""Logging configuration: UTC timestamps, correlation ID, PAN masking."""
from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from typing import Optional

# Matches 13–19 digit sequences that look like payment card numbers.
_PAN_PATTERN = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{1,7}\b")


class _AuditFilter(logging.Filter):
    """Injects correlation_id and masks card-number-like patterns in log messages."""

    def __init__(self, correlation_id: str) -> None:
        super().__init__()
        self.correlation_id = correlation_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = self.correlation_id  # type: ignore[attr-defined]
        if isinstance(record.msg, str):
            record.msg = _PAN_PATTERN.sub("[PAN MASKED]", record.msg)
        return True


def setup_logging(
    correlation_id: Optional[str] = None,
    level: int = logging.INFO,
) -> str:
    """Configure root logger. Returns the correlation_id in use (auto-generated if None)."""
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_AuditFilter(correlation_id))

    fmt = logging.Formatter(
        fmt="%(asctime)s UTC [%(correlation_id)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fmt.converter = time.gmtime  # force UTC
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    return correlation_id
