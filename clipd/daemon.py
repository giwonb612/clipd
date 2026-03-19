import logging
import signal
import sys
import time
from pathlib import Path

LOG_PATH = Path.home() / ".clipd" / "daemon.log"


def run_daemon():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # stdout is redirected to LOG_PATH by launchd — use stdout only to avoid
    # double-writing when both FileHandler and launchd redirection are active.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
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
