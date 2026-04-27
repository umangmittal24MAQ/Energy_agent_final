"""
Logging configuration for the Energy Dashboard application
"""
import logging
import logging.config
import os
from pathlib import Path
from typing import Optional

# Create logs directory if it doesn't exist
LOG_DIR = Path(__file__).parent.parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Logging configuration dictionary
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "detailed": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "INFO",
            "formatter": "detailed",
            "filename": os.getenv("LOG_FILE_PATH", str(LOG_DIR / "app.log")),
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "ERROR",
            "formatter": "detailed",
            "filename": os.getenv("LOG_FILE_PATH", str(LOG_DIR / "errors.log")),
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
        },
    },
    "loggers": {
        "": {
            "level": "INFO",
            "handlers": ["console", "file", "error_file"],
        },
        "uvicorn.access": {
            "level": "INFO",
            "handlers": ["file"],
            "propagate": False,
        },
        "sqlalchemy": {
            "level": "WARNING",
            "handlers": ["file"],
            "propagate": False,
        },
    },
}


def setup_logging(log_level: Optional[str] = None) -> None:
    """
    Setup logging configuration

    Args:
        log_level: Optional logging level override (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    logging.config.dictConfig(LOGGING_CONFIG)

    if log_level:
        logger = logging.getLogger()
        logger.setLevel(getattr(logging, log_level.upper()))


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for the given name

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


# Initialize logging and export default logger
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger("app")
