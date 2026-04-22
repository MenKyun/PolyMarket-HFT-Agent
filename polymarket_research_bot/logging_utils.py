from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if hasattr(record, "fields") and isinstance(record.fields, dict):
            payload.update(record.fields)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class EventAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        event = kwargs.pop("event", None)
        fields = kwargs.pop("fields", None) or {}
        extra = kwargs.setdefault("extra", {})
        extra["event"] = event or msg.replace(" ", "_").lower()
        extra["fields"] = fields
        return msg, kwargs



def setup_logging(logs_dir: str, level: str = "INFO") -> EventAdapter:
    logger = logging.getLogger("polymarket_research_bot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    formatter = JsonFormatter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(logs_dir, "bot.jsonl"),
        maxBytes=20_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return EventAdapter(logger, {})
