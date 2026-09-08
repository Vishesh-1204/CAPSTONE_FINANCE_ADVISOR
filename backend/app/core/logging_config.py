# app/core/logging_config.py

import logging
import sys


def configure_logging() -> None:
    """
    Configures the root logger for the entire application.
    Called once when the FastAPI app starts up.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )