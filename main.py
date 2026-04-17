import logging
import signal
import sys
import time

import config
from agent import run_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("main")

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    log.info("signal %s received, stopping after current cycle", signum)
    _stop = True


def main():
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    log.info(
        "starting loop interval=%ss lookback=%sh threshold=%s dry_run=%s",
        config.POLL_INTERVAL_SECONDS,
        config.TRASH_LOOKBACK_HOURS,
        config.CONFIDENCE_THRESHOLD,
        config.DRY_RUN,
    )
    while not _stop:
        try:
            result = run_once()
            log.info(
                "cycle done: learned=%d candidates=%d moved=%d",
                result.get("new_rules_this_run", 0),
                len(result.get("candidates", [])),
                len(result.get("moved", [])),
            )
        except Exception:
            log.exception("cycle failed")
        for _ in range(config.POLL_INTERVAL_SECONDS):
            if _stop:
                break
            time.sleep(1)
    log.info("exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
