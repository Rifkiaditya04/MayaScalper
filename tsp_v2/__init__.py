"""TSP V2 package."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from .app import main as app_main

    return app_main(argv)


__all__ = ["main"]
