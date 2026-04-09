#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import signal
import sys

from config import ConfigManager
from core import GasMonitorCore
from logging_utils import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Industrial gas monitoring service")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    args = parser.parse_args()

    config_manager = ConfigManager(args.config)
    runtime = config_manager.runtime()
    configure_logging(runtime.log_file)
    logging.info("starting gas monitor service")

    core = GasMonitorCore(config_manager)

    def handle_signal(signum, _frame) -> None:
        logging.info("received signal %s, shutting down", signum)
        core.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        core.run()
    except KeyboardInterrupt:
        core.stop()
    except Exception:
        logging.exception("fatal runtime error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
