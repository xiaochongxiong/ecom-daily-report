"""日志。

为什么值得单独写一个文件：定时任务在半夜跑，出问题时你只有日志可看。
日志要落文件并自动切分，不能只 print。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import settings

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return

    settings.ensure_dirs()
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # 单文件 5MB，保留 5 个历史文件
    file_handler = RotatingFileHandler(
        settings.log_dir / "pipeline.log", maxBytes=5 * 1024 * 1024,
        backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
