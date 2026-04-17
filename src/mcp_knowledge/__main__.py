"""Entry point for crows-nest — unified stdio/HTTP/scheduler mode.

The sys.path insertion for pipeline imports lives here so that the rest of
the package (mcp_adapter, api) can do a clean `from pipeline.db import ...`
without worrying about path setup.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pipeline path — insert project root so `from pipeline.xxx import ...` works.
# Must happen before any crows-nest imports that touch pipeline modules.
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from service_base import run_service  # noqa: E402

from .api import create_api  # noqa: E402
from .mcp_adapter import create_mcp_server  # noqa: E402
from .scheduler import create_scheduler  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    scheduler = create_scheduler()
    mcp = create_mcp_server()
    api = create_api(scheduler=scheduler)

    run_service(
        mcp_server=mcp,
        api_app=api,
        scheduler=scheduler,
        service_name="crows-nest",
        default_port=27185,
    )


if __name__ == "__main__":
    main()
