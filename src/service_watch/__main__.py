"""Entry point. Wires the modules together and starts the loop."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = Path(os.environ.get("CONFIG_PATH", "/etc/service-watch/config.yaml"))
    if not config_path.exists():
        logging.error("config file not found at %s", config_path)
        return 1

    # Late imports so logging is configured before any module loads.
    from .config import load_config
    from .notifier import WebexNotifier
    from .probe import HttpProber
    from .state import StateMachineImpl
    from .orchestrator import Orchestrator

    config = load_config(config_path)

    webex_token = os.environ.get("WEBEX_BOT_TOKEN")
    webex_space_id = os.environ.get("WEBEX_SPACE_ID")
    if not webex_token or not webex_space_id:
        logging.error("WEBEX_BOT_TOKEN and WEBEX_SPACE_ID must be set in the environment")
        return 1

    orchestrator = Orchestrator(
        config=config,
        prober=HttpProber(),
        state_machine=StateMachineImpl(),
        notifier=WebexNotifier(token=webex_token, space_id=webex_space_id),
    )
    try:
        orchestrator.run_forever()
    except KeyboardInterrupt:
        logging.info("service-watch shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
