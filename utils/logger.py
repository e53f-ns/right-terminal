import logging
import os
from datetime import datetime


def get_logger(
    name: str,
    log_dir: str = "logs",
    log_to_file: bool = True,
    level: str = "INFO",
    console: bool = True,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger  # already configured, avoid duplicate handlers

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    if log_to_file:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(
            os.path.join(log_dir, f"agent_{datetime.utcnow().strftime('%Y%m%d')}.log")
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
