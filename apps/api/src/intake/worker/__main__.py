"""Worker entrypoint. The job runner arrives in M2; for now it idles so compose stays up."""

import logging
import time

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("intake.worker")


def main() -> None:
    log.info("worker started (no jobs implemented yet)")
    while True:
        time.sleep(30)


if __name__ == "__main__":
    main()
