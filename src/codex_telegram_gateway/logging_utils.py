from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, self.datefmt),
        }
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = JsonFormatter()
    if not root.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
        file_handler = RotatingFileHandler(log_dir / "gateway.log", maxBytes=10_000_000, backupCount=5)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    return logging.getLogger("codex_telegram_gateway")


def log_extra(logger: logging.Logger, message: str, **fields: object) -> None:
    logger.info(message, extra={"extra_fields": fields})
