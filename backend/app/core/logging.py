"""Logging configuration driven by settings.log_level."""

import logging
import logging.config

from app.core.config import get_settings

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def configure_logging(level: str | None = None) -> None:
    resolved = (level or get_settings().log_level).upper()
    if resolved not in logging.getLevelNamesMapping():
        resolved = "INFO"
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": {"format": LOG_FORMAT}},
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"}
            },
            "loggers": {
                "app": {"level": resolved, "handlers": ["console"], "propagate": False},
                "sqlalchemy.engine": {"level": "INFO" if resolved == "DEBUG" else "WARNING"},
            },
        }
    )
