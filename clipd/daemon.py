import logging
import logging.handlers
import signal
import sys
import time
from pathlib import Path

LOG_PATH = Path.home() / ".clipd" / "daemon.log"
LOG_MAX_BYTES = 1 * 1024 * 1024   # 1 MB per file
LOG_BACKUP_COUNT = 3               # keep daemon.log, .1, .2, .3


def run_daemon():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    # Rotating file handler — max 1 MB, keep 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        str(LOG_PATH),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    # Also echo to stdout so launchd captures it (but NOT to the log file again)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    logger = logging.getLogger(__name__)

    from clipd.clipboard import read_clipboard
    from clipd.db import Database
    from clipd.ocr import extract_text_from_image

    db = Database()
    last_hash: str | None = None

    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}, shutting down")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("clipd daemon started")

    while True:
        try:
            clip_type, content, hash_ = read_clipboard()
            if clip_type and hash_ and hash_ != last_hash:
                last_hash = hash_
                ocr_text = ""
                if clip_type == "image":
                    try:
                        ocr_text = extract_text_from_image(content)
                        if ocr_text:
                            logger.info(f"OCR: {len(ocr_text)} chars extracted")
                    except Exception as e:
                        logger.warning(f"OCR failed: {e}")
                row_id = db.insert(clip_type, content, ocr_text, hash_)
                if row_id:
                    logger.info(f"Saved {clip_type} clip id={row_id} hash={hash_[:8]}")
        except Exception as e:
            logger.error(f"Daemon error: {e}")

        time.sleep(1.0)
