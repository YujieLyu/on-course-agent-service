import logging
import os



def get_log_level() -> str:
    return os.getenv("LOG_LEVEL", "INFO")


def get_log_format() -> str:
    return os.getenv(
        "LOG_FORMAT",
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def get_logger(name: str = __name__) -> logging.Logger:
    """Create a logger based on environment variable LOG_LEVEL.

    Args:
        name (str): Name of the logger. Defaults to the module's name.

    Returns:
        logging.Logger: Configured logger instance.

    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(getattr(logging, get_log_level(), logging.INFO))

        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(get_log_format()))
        logger.addHandler(handler)
        logger.propagate = False

    return logger