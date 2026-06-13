"""CLI entrypoint compatibility wrapper for TSP V2."""

from __future__ import annotations

from .run_v2 import main


if __name__ == "__main__":
    raise SystemExit(main())
