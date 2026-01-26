# -*- coding: utf-8 -*-
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

def setup_logging(app):
    """Configura RotatingFileHandler em <project_root>/logs/app.log, sem duplicar handlers."""
    base_dir = Path(__file__).resolve().parent.parent
    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    log_path = log_dir / "app.log"

    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

    # Evita duplicar handlers (comum com reloader/debug)
    if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
        app.logger.addHandler(handler)

    app.logger.setLevel(logging.INFO)
    app.logger.info("Logging iniciado.")