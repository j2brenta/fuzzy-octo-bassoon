"""Logging for the long-running processes (the bot, the jury frontend): one line per pipeline stage and per model call.

INFO by default; LOG_LEVEL=DEBUG also shows aiogram's per-update lines, LOG_LEVEL=WARNING only problems. Lines name
items by id and say what a stage decided (labels, counts, timings), never the captured text or the claims.
"""
import logging
import os

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure(env=None) -> None:
    env = os.environ if env is None else env
    name = (env.get("LOG_LEVEL") or "INFO").strip().upper()
    level = logging.getLevelName(name) if isinstance(logging.getLevelName(name), int) else logging.INFO
    logging.basicConfig(level=level, format=FORMAT, force=True)
    if level > logging.DEBUG:
        logging.getLogger("aiogram.event").setLevel(logging.WARNING)  # "Update id=… is handled" per message
