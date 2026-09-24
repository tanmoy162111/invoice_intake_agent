"""Worker entrypoint: python -m intake.worker"""

import logging
import os

from intake.config import get_settings
from intake.db.session import make_engine
from intake.ingest.storage import LocalStorage
from intake.worker.runner import run_forever

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Provider SDK and HTTP loggers can print request bodies (base64 page images) at DEBUG.
for noisy in ("anthropic", "httpx", "httpx2", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    settings = get_settings()
    run_forever(make_engine(), LocalStorage(settings.storage_dir), settings)


if __name__ == "__main__":
    main()
